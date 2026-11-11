"""Offline material reuse manager. All imported content is stored in SQLite."""
import argparse
import base64
from collections import Counter, defaultdict
from contextlib import contextmanager, closing
from datetime import datetime
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading
from time import monotonic
import urllib.parse
from urllib.request import ProxyHandler, Request, build_opener
import webbrowser
import zipfile

from openpyxl import load_workbook, Workbook

BASE = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
ASSETS = Path(getattr(sys, '_MEIPASS', BASE)) / 'web'

def source_revision():
    digest = sha256(Path(__file__).read_bytes())
    for name in ('index.html', 'app.js', 'style.css', 'byd-logo.svg'):
        digest.update((ASSETS / name).read_bytes())
    return digest.hexdigest()

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(version INTEGER NOT NULL);
INSERT INTO meta SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM meta);
CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY,category_id INTEGER NOT NULL REFERENCES categories(id),name TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,UNIQUE(category_id,name));
CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY,project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,filename TEXT NOT NULL,hash TEXT NOT NULL,created TEXT NOT NULL,original BLOB NOT NULL,rows_json TEXT NOT NULL,warnings_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY,created TEXT NOT NULL,result_json TEXT NOT NULL);
'''

def upgrade_schema(db):
    version = db.execute('SELECT version FROM meta').fetchone()[0]
    if version == 2:
        return
    if version != 1:
        raise ValueError('数据库版本不支持')
    db.execute('PRAGMA foreign_keys=OFF')
    db.executescript('''
        BEGIN;
        CREATE TABLE categories_v2 (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
            parent_id INTEGER REFERENCES categories(id)
        );
        INSERT INTO categories_v2(id,name,deleted) SELECT id,name,deleted FROM categories;
        DROP TABLE categories;
        ALTER TABLE categories_v2 RENAME TO categories;
        CREATE UNIQUE INDEX category_name_per_parent ON categories(COALESCE(parent_id, 0), name);
        UPDATE meta SET version=2;
        COMMIT;
    ''')
    db.execute('PRAGMA foreign_keys=ON')

def now():
    return datetime.now().isoformat(timespec='seconds')

def parse_excel(content):
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    rows, warnings = [], []
    try:
        for sheet in wb:
            header = None
            nonempty = False
            for number, values in enumerate(sheet.iter_rows(values_only=True), 1):
                cells = [str(v).strip() if v is not None else '' for v in values]
                if not any(cells):
                    continue
                nonempty = True
                if header is None:
                    if '子阶料号' in cells and '借用件/新开件' in cells:
                        header = (cells.index('子阶料号'), cells.index('借用件/新开件'))
                    continue
                a, b = header
                code = cells[a] if a < len(cells) else ''
                kind = cells[b] if b < len(cells) else ''
                if code == '子阶料号' and kind == '借用件/新开件':
                    continue
                if not code:
                    warnings.append(f'{sheet.title} 第{number}行：缺少物料编码，已跳过')
                    continue
                if kind not in ('借用件', '新开件'):
                    raise ValueError(f'{sheet.title} 第{number}行：分类必须是借用件或新开件（当前：{kind or "空白"}）')
                rows.append(dict(code=code,kind=kind,sheet=sheet.title,row=number))
            if nonempty and header is None:
                warnings.append(f'{sheet.title}：未找到所需表头，已跳过')
        if not rows:
            raise ValueError('未找到有效物料。表头需要包含“子阶料号”和“借用件/新开件”。')
        counts = Counter(r['code'] for r in rows)
        for code, count in counts.items():
            if count > 1:
                warnings.append(f'项目内部编码 {code} 出现{count}次；保留原始行，不单独认定为跨项目重复')
        return rows, warnings
    finally:
        wb.close()

class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'reuse.sqlite3'
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript(SCHEMA)
            upgrade_schema(db)

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    def backup(self):
        folder = self.directory / 'backups'
        folder.mkdir(exist_ok=True)
        path = folder / (datetime.now().strftime('%Y%m%d-%H%M%S-') + secrets.token_hex(3) + '.sqlite3')
        with self.db() as source, closing(sqlite3.connect(path)) as target:
            source.backup(target)
        return path

    def projects(self, db, category):
        return [dict(r) for r in db.execute('''SELECT p.*,v.id version_id,v.filename,v.created,v.hash,v.rows_json,v.warnings_json,
          (SELECT COUNT(*) FROM versions WHERE project_id=p.id) version_count
          FROM projects p JOIN versions v ON v.id=(SELECT MAX(id) FROM versions WHERE project_id=p.id)
          WHERE p.category_id=? AND p.deleted=0 ORDER BY p.id''', (category,))]

    def calculate(self, projects):
        occurrences = defaultdict(list)
        for p in projects:
            for r in json.loads(p['rows_json']):
                occurrences[r['code']].append(dict(r, project=p['name'], project_id=p['id']))
        duplicates = {code for code, values in occurrences.items() if len({v['project_id'] for v in values}) > 1}
        total = sum(len(v) for v in occurrences.values())
        borrowed = sum(v['kind']=='借用件' for code, values in occurrences.items() if code not in duplicates for v in values)
        raw_borrowed = sum(v['kind']=='借用件' for values in occurrences.values() for v in values)
        numerator, denominator = len(duplicates)+borrowed, total-len(duplicates)
        unverified = [dict(code=code,occurrences=values) for code,values in occurrences.items() if code not in duplicates and any(v['kind']=='借用件' for v in values)]
        return dict(total=total,unique=len(occurrences),duplicate_count=len(duplicates),borrowed=borrowed,raw_borrowed=raw_borrowed,new=total-raw_borrowed,
                    numerator=numerator,denominator=denominator,rate=numerator/denominator if denominator else None,
                    duplicates=[dict(code=code,occurrences=occurrences[code]) for code in sorted(duplicates)],unverified=unverified)

    def state(self):
        with self.db() as db:
            categories=[]
            for c in db.execute('''
                SELECT c.* FROM categories c LEFT JOIN categories parent ON c.parent_id=parent.id
                WHERE c.deleted=0 AND (c.parent_id IS NULL OR parent.deleted=0) ORDER BY c.id
            ''').fetchall():
                projects=self.projects(db,c['id'])
                result=self.calculate(projects)
                for p in projects:
                    p['rows']=json.loads(p.pop('rows_json'))
                    p['warnings']=json.loads(p.pop('warnings_json'))
                categories.append(dict(c,projects=projects,result=result))
            children = defaultdict(list)
            group_ids = {row[0] for row in db.execute('SELECT DISTINCT parent_id FROM categories WHERE parent_id IS NOT NULL')}
            for c in categories:
                if c['parent_id'] is not None:
                    children[c['parent_id']].append(c)
            for c in categories:
                group = children[c['id']]
                c['is_group'] = c['id'] in group_ids
                c['project_count'] = len(c['projects'])
                if c['is_group']:
                    rates = [child['result']['rate'] for child in group if child['result']['rate'] is not None]
                    c['project_count'] = sum(len(child['projects']) for child in group)
                    c['result'] = {
                        'rate': sum(rates) / len(rates) if rates else None,
                        'total': sum(child['result']['total'] for child in group),
                        'rated_children': len(rates),
                    }
            trash=[dict(r,kind='category') for r in db.execute('''
                SELECT c.id,c.name,parent.name category FROM categories c
                LEFT JOIN categories parent ON c.parent_id=parent.id
                WHERE c.deleted=1 AND (c.parent_id IS NULL OR parent.deleted=0)
            ''')]
            trash += [dict(r,kind='project') for r in db.execute('''
                SELECT p.id,p.name,CASE WHEN parent.id IS NULL THEN c.name ELSE parent.name || ' / ' || c.name END category
                FROM projects p JOIN categories c ON c.id=p.category_id LEFT JOIN categories parent ON c.parent_id=parent.id
                WHERE p.deleted=1 AND c.deleted=0 AND (c.parent_id IS NULL OR parent.deleted=0)
            ''')]
            return dict(categories=categories,trash=trash)

    def active_leaf(self, db, category):
        row = db.execute('''
            SELECT c.* FROM categories c LEFT JOIN categories parent ON c.parent_id=parent.id
            WHERE c.id=? AND c.deleted=0 AND (c.parent_id IS NULL OR parent.deleted=0)
        ''', (category,)).fetchone()
        if not row:
            raise ValueError('分类不存在')
        if db.execute('SELECT 1 FROM categories WHERE parent_id=?', (category,)).fetchone():
            raise ValueError('请选择最小分类')
        return row

    def preview(self, data):
        category=int(data['category'])
        with self.db() as db:
            self.active_leaf(db, category)
            existing={r['name']:r for r in self.projects(db,category)}
            explicit=data.get('project_id')
            update=next((r for r in existing.values() if r['id']==int(explicit)),None) if explicit else None
            if explicit and (not update or len(data['files'])!=1):
                raise ValueError('更新原项目时请选择一个有效项目和一份表格')
            files=[]
            names=set()
            for f in data['files']:
                name=update['name'] if update else Path(f['name']).stem.strip()
                if not name or name in names:
                    raise ValueError('本次导入有同名项目，请分别导入或调整项目名称')
                names.add(name)
                content=base64.b64decode(f['content'],validate=True)
                rows,warnings=parse_excel(content)
                old=existing.get(name)
                digest=sha256(content).hexdigest()
                status='未变化' if old and old['hash']==digest else ('更新' if old else '新增')
                matches=[n for n,p in existing.items() if n!=name and p['hash']==digest]
                if matches:
                    warnings.append('文件内容与已有项目 '+ '、'.join(matches)+' 相同，请确认是否为不同项目')
                if db.execute('SELECT 1 FROM projects WHERE category_id=? AND name=? AND deleted=1',(category,name)).fetchone():
                    raise ValueError(f'{name} 已在回收站，请先恢复或永久删除')
                sheets=dict(Counter(row['sheet'] for row in rows))
                files.append(dict(name=name,filename=f['name'],count=len(rows),sheets=sheets,borrowed=sum(r['kind']=='借用件' for r in rows),warnings=warnings,status=status))
            return dict(files=files)

    def mutate(self, action, data):
        with self.lock:
            return self._mutate(action, data)

    def _mutate(self, action, data):
        if action=='preview':
            return self.preview(data)
        if action=='import':
            preview=self.preview(data)
            prepared=[]
            for f, item in zip(data['files'],preview['files']):
                content=base64.b64decode(f['content'],validate=True)
                rows,warnings=parse_excel(content)
                prepared.append((item,content,rows,warnings))
            self.backup()
            with self.db() as db:
                for item,content,rows,warnings in prepared:
                    if item['status']=='未变化': continue
                    db.execute('INSERT OR IGNORE INTO projects(category_id,name) VALUES(?,?)',(data['category'],item['name']))
                    pid=db.execute('SELECT id FROM projects WHERE category_id=? AND name=?',(data['category'],item['name'])).fetchone()[0]
                    db.execute('INSERT INTO versions(project_id,filename,hash,created,original,rows_json,warnings_json) VALUES(?,?,?,?,?,?,?)',
                               (pid,item['filename'],sha256(content).hexdigest(),now(),content,json.dumps(rows,ensure_ascii=False),json.dumps(warnings,ensure_ascii=False)))
            return preview
        self.backup()
        with self.db() as db:
            if action=='category':
                name=str(data['name']).strip()
                if not name: raise ValueError('请填写分类名称')
                if data.get('id'):
                    db.execute('UPDATE categories SET name=? WHERE id=?',(name,data['id']))
                else:
                    parent = data.get('parent_id')
                    if parent is not None:
                        if not db.execute('SELECT 1 FROM categories WHERE id=? AND deleted=0 AND parent_id IS NULL', (parent,)).fetchone():
                            raise ValueError('请选择有效的大分类')
                        if db.execute('SELECT 1 FROM projects WHERE category_id=?', (parent,)).fetchone():
                            ungrouped = db.execute("SELECT id FROM categories WHERE parent_id=? AND name='未分组'", (parent,)).fetchone()
                            if ungrouped:
                                target = ungrouped['id']
                                db.execute('UPDATE categories SET deleted=0 WHERE id=?', (target,))
                            else:
                                target = db.execute("INSERT INTO categories(name,parent_id) VALUES('未分组',?)", (parent,)).lastrowid
                            db.execute('UPDATE projects SET category_id=? WHERE category_id=?', (target,parent))
                            if name == '未分组':
                                return dict(ok=True)
                    db.execute('INSERT INTO categories(name,parent_id) VALUES(?,?)',(name,parent))
            elif action=='move_project':
                project = db.execute('SELECT * FROM projects WHERE id=? AND deleted=0', (data['id'],)).fetchone()
                if not project:
                    raise ValueError('项目不存在')
                source = self.active_leaf(db, project['category_id'])
                target = self.active_leaf(db, data['category'])
                if source['parent_id'] is None or source['parent_id'] != target['parent_id']:
                    raise ValueError('请选择同一大分类下的小分类')
                db.execute('UPDATE projects SET category_id=? WHERE id=?', (target['id'], project['id']))
            elif action=='trash_many':
                ids=tuple(dict.fromkeys(int(i) for i in data['ids']))
                if not ids: raise ValueError('请选择项目')
                for ident in ids:
                    db.execute('UPDATE projects SET deleted=1 WHERE id=? AND category_id=?',(ident,data['category']))
            elif action in ('trash','restore','purge'):
                table='categories' if data['kind']=='category' else 'projects'
                ident=int(data['id'])
                record=db.execute(f'SELECT * FROM {table} WHERE id=?',(ident,)).fetchone()
                if not record: raise ValueError('数据不存在')
                if action=='purge':
                    if not record['deleted']: raise ValueError('请先移入回收站')
                    if table=='categories':
                        db.execute('DELETE FROM projects WHERE category_id IN (SELECT id FROM categories WHERE id=? OR parent_id=?)',(ident,ident))
                        db.execute('DELETE FROM categories WHERE parent_id=?',(ident,))
                    db.execute(f'DELETE FROM {table} WHERE id=?',(ident,))
                    affected=ident if table=='categories' else record['category_id']
                    for snap in db.execute('SELECT id,result_json FROM snapshots').fetchall():
                        old=json.loads(snap['result_json'])
                        remaining=[c for c in old if c['id']!=affected]
                        if len(old)==len(remaining): continue
                        if remaining:
                            db.execute('UPDATE snapshots SET result_json=? WHERE id=?',(json.dumps(remaining,ensure_ascii=False),snap['id']))
                        else: db.execute('DELETE FROM snapshots WHERE id=?',(snap['id'],))
                else:
                    if action=='restore' and table=='categories' and record['parent_id'] is not None:
                        parent = record['parent_id']
                        if not db.execute('SELECT 1 FROM categories WHERE id=? AND deleted=0', (parent,)).fetchone():
                            raise ValueError('请先恢复所属大分类')
                        if db.execute('SELECT 1 FROM projects WHERE category_id=?', (parent,)).fetchone():
                            raise ValueError('请先新建小分类，将大分类直属项目归入未分组')
                    db.execute(f'UPDATE {table} SET deleted=? WHERE id=?',(1 if action=='trash' else 0,ident))
            else:
                raise ValueError('未知操作')
        if action=='purge':
            with self.db() as db: db.execute('VACUUM')
        return dict(ok=True)

    def migration(self):
        path=self.backup()
        content=path.read_bytes()
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('reuse.sqlite3',content)
            z.writestr('manifest.json',json.dumps(dict(version=1,sha256=sha256(content).hexdigest(),created=now())))
        return out.getvalue()

    def restore_package(self, content):
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            if set(z.namelist()) != {'reuse.sqlite3','manifest.json'}: raise ValueError('迁移包内容不正确')
            if any(v.file_size>512*1024*1024 for v in z.infolist()): raise ValueError('迁移包过大')
            manifest=json.loads(z.read('manifest.json'))
            raw=z.read('reuse.sqlite3')
            if manifest.get('version')!=1 or manifest.get('sha256')!=sha256(raw).hexdigest(): raise ValueError('迁移包版本不支持或完整性校验失败')
        temp=self.directory / ('restore-'+secrets.token_hex(8)+'.sqlite3')
        try:
            temp.write_bytes(raw)
            with closing(sqlite3.connect(temp)) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('数据库损坏')
                if db.execute('SELECT version FROM meta').fetchall() not in ([(1,)], [(2,)]): raise ValueError('数据库版本不支持')
                if db.execute('PRAGMA foreign_key_check').fetchall(): raise ValueError('数据库关联数据损坏')
                for table in ('categories','projects','versions','snapshots'): db.execute(f'SELECT * FROM {table} LIMIT 1')
                upgrade_schema(db)
            candidate=Store.__new__(Store)
            candidate.directory=self.directory; candidate.path=temp; candidate.lock=threading.RLock()
            candidate.state()
            with self.lock:
                self.backup()
                os.replace(temp,self.path)
        finally:
            temp.unlink(missing_ok=True)

    def report(self):
        wb=Workbook(); summary=wb.active; summary.title='分类排名'
        summary.append(['排名','分类','项目数','物料总记录数','重复编码种类数','排除重复后的借用件数','分子','分母','复用率'])
        details=wb.create_sheet('重复编码明细'); details.append(['分类','物料编码','项目','分类标记','工作表','原始行'])
        records=wb.create_sheet('全部物料'); records.append(['分类','项目','物料编码','分类标记','工作表','原始行'])
        active = self.state()['categories']
        names = {c['id']: c['name'] for c in active}
        categories=sorted((c for c in active if not c['is_group']),key=lambda c:c['result']['rate'] if c['result']['rate'] is not None else -1,reverse=True)
        rank=0
        for c in categories:
            label = c['name'] if c['parent_id'] is None else names[c['parent_id']] + ' / ' + c['name']
            r=c['result']; rank+= r['rate'] is not None
            summary.append([rank if r['rate'] is not None else None,label,len(c['projects']),r['total'],r['duplicate_count'],r['borrowed'],r['numerator'],r['denominator'],r['rate']])
            summary.cell(summary.max_row,9).number_format='0.00%'
            for d in r['duplicates']:
                for o in d['occurrences']: details.append([label,d['code'],o['project'],o['kind'],o['sheet'],o['row']])
            for p in c['projects']:
                for row in p['rows']: records.append([label,p['name'],row['code'],row['kind'],row['sheet'],row['row']])
        for sheet in wb:
            sheet.freeze_panes='A2'; sheet.auto_filter.ref=sheet.dimensions
            for col in sheet.columns:
                sheet.column_dimensions[col[0].column_letter].width=24
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value,str): cell.data_type='s'
        out=io.BytesIO(); wb.save(out); return out.getvalue()

class BrowserLifetime:
    """Keep the service alive while at least one browser tab checks in."""

    heartbeat_timeout = 90
    close_grace = 6

    def __init__(self, wait_for_browser):
        self.tabs = {}
        self.lock = threading.Lock()
        self.deadline = monotonic() + self.heartbeat_timeout if wait_for_browser else float('inf')

    def update(self, tab, closing=False):
        with self.lock:
            now = monotonic()
            if closing:
                if self.tabs.pop(tab, None) is not None and not self.tabs:
                    self.deadline = now + self.close_grace
            else:
                self.tabs[tab] = now + self.heartbeat_timeout
                self.deadline = now

    def expired(self):
        with self.lock:
            now = monotonic()
            self.tabs = {tab: deadline for tab, deadline in self.tabs.items() if deadline > now}
            if self.tabs:
                return False
            return now >= self.deadline


def serve(store, port=0, browser=True, address_file=None):
    token=secrets.token_urlsafe(32)
    revision = source_revision()
    lifetime = BrowserLifetime(browser)
    class LocalServer(ThreadingHTTPServer):
        daemon_threads = False

        def service_actions(self):
            if lifetime.expired():
                threading.Thread(target=self.shutdown, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def respond(self,content,status=200,mime='application/json; charset=utf-8',filename=None):
            if not isinstance(content,bytes): content=json.dumps(content,ensure_ascii=False).encode()
            self.send_response(status); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('X-Reuse-Revision', revision)
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            if filename: self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
            self.end_headers(); self.wfile.write(content)
        def authorized(self):
            host=self.headers.get('Host','')
            return host==f'127.0.0.1:{self.server.server_port}' and self.headers.get('X-App-Token')==token
        def do_GET(self):
            path=urllib.parse.urlsplit(self.path).path
            if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}': return self.respond({'error':'无效访问'},403)
            try:
                if path=='/': self.respond((ASSETS/'index.html').read_bytes().replace(b'__TOKEN__',token.encode()),mime='text/html; charset=utf-8')
                elif path in ('/app.js','/style.css','/byd-logo.svg'):
                    mime = {'/app.js': 'text/javascript; charset=utf-8', '/style.css': 'text/css; charset=utf-8', '/byd-logo.svg': 'image/svg+xml'}
                    self.respond((ASSETS/path[1:]).read_bytes(), mime=mime[path])
                elif not self.authorized(): self.respond({'error':'访问校验失败'},403)
                elif path=='/api/state': self.respond(store.state())
                elif path=='/api/export': self.respond(store.migration(),mime='application/zip',filename='ProductReuseRate-backup.zip')
                elif path=='/api/report': self.respond(store.report(),mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',filename='ReuseRate-report.xlsx')
                elif path=='/api/versions':
                    ident=int(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)['id'][0])
                    with store.db() as db:
                        versions=[dict(row) for row in db.execute('SELECT id,filename,created FROM versions WHERE project_id=? ORDER BY id DESC',(ident,))]
                    self.respond(versions)
                elif path=='/api/original':
                    ident=int(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)['id'][0])
                    with store.db() as db:
                        row=db.execute('SELECT original FROM versions WHERE id=?',(ident,)).fetchone()
                        if not row: raise ValueError('原表不存在')
                        self.respond(row[0],mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',filename='original.xlsx')
                else: self.respond({'error':'不存在'},404)
            except Exception as e: self.respond({'error':str(e)},400)
        def do_POST(self):
            if not self.authorized(): return self.respond({'error':'访问校验失败'},403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<=0 or length>200*1024*1024: raise ValueError('请求为空或超过200MB，请分批导入')
                data=json.loads(self.rfile.read(length))
                action=self.path.removeprefix('/api/')
                if action=='shutdown':
                    self.respond({'ok':True}); threading.Thread(target=self.server.shutdown,daemon=True).start(); return
                if action in ('tab-open','tab-close'):
                    tab = data.get('tab')
                    if not isinstance(tab, str) or not 1 <= len(tab) <= 64:
                        raise ValueError('无效的页面标识')
                    lifetime.update(tab, closing=action=='tab-close')
                    self.respond({'ok':True})
                    return
                if action=='migration':
                    store.restore_package(base64.b64decode(data['content'],validate=True)); result={'ok':True}
                else: result=store.mutate(action,data)
                self.respond(result)
            except sqlite3.IntegrityError: self.respond({'error':'名称已存在，请使用其他名称（也请检查回收站）'},400)
            except Exception as e: self.respond({'error':str(e)},400)
    server=LocalServer(('127.0.0.1',port),Handler)
    url=f'http://127.0.0.1:{server.server_port}'
    if address_file: address_file.write_text(url,encoding='ascii')
    print(url,flush=True)
    if browser: threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    finally:
        server.server_close()
        if address_file: address_file.unlink(missing_ok=True)

def running_instance(url, revision):
    """Return the token only for a recognized, outdated local instance."""
    if not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', url):
        raise RuntimeError('本地工具启动地址无效')
    client = build_opener(ProxyHandler({}))
    with client.open(url, timeout=1) as response:
        if response.headers.get('X-Reuse-Revision') == revision:
            return None
        page = response.read(100_000).decode('utf-8')
    token = re.search(r'<meta name="app-token" content="([A-Za-z0-9_-]{43})">', page)
    if not token or '物料复用' not in page:
        raise RuntimeError('无法识别正在运行的本地工具')
    request = Request(url + '/api/shutdown', data=b'{}', headers={
        'X-App-Token': token.group(1), 'Content-Type': 'application/json',
    })
    with client.open(request, timeout=2) as response:
        if not json.load(response).get('ok'):
            raise RuntimeError('旧版工具退出失败')
    return True


def launch(directory, port, browser):
    if os.name!='nt':
        return serve(Store(directory),port,browser)
    import msvcrt
    import time
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    address_file=directory/'running.url.txt'
    revision = source_revision()
    with (directory/'instance.lock').open('a+b') as lock:
        if lock.tell()==0:
            lock.write(b'1'); lock.flush()
        lock.seek(0)
        checked_url = None
        for _ in range(40):
            try:
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                break
            except OSError:
                try:
                    url = address_file.read_text(encoding='ascii').strip()
                    if url and url != checked_url:
                        if running_instance(url, revision) is None:
                            if browser: webbrowser.open(url)
                            return
                        checked_url = url
                except OSError:
                    pass
                time.sleep(.15)
        else:
            raise RuntimeError('已有程序正在启动，请稍后再试')
        try:
            address_file.unlink(missing_ok=True)
            serve(Store(directory),port,browser,address_file)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--data-dir',default=str(BASE/'data')); parser.add_argument('--port',type=int,default=0); parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    launch(args.data_dir,args.port,not args.no_browser and os.environ.get('PRODUCT_REUSE_RATE_NO_BROWSER')!='1')
