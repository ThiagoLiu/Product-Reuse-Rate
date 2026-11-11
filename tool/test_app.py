import base64
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from openpyxl import Workbook
from app import SCHEMA, Store, parse_excel


def excel(rows):
    wb=Workbook(); ws=wb.active
    ws.append(['子阶料号','借用件/新开件'])
    for row in rows: ws.append(row)
    out=io.BytesIO(); wb.save(out); return out.getvalue()


def file(name,rows):
    return dict(name=name+'.xlsx',content=base64.b64encode(excel(rows)).decode())


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(self.tmp.name)
        self.store.mutate('category',{'name':'座椅'})
        self.cid=self.store.state()['categories'][0]['id']
    def tearDown(self): self.tmp.cleanup()
    def load(self,files): return self.store.mutate('import',dict(category=self.cid,files=files))
    def category(self): return self.store.state()['categories'][0]

    def test_real_samples_and_persistence(self):
        folder=Path(__file__).resolve().parents[2]/'座椅'
        files=[dict(name=p.name,content=base64.b64encode(p.read_bytes()).decode()) for p in sorted(folder.glob('*.xlsx'))]
        if not files:
            self.skipTest('需要本机的座椅样表')
        self.assertEqual(len(files),2)
        self.load(files); r=self.category()['result']
        self.assertEqual((r['total'],r['duplicate_count'],r['borrowed'],r['rate']),(42,2,22,.6))
        self.store=Store(self.tmp.name)
        self.assertEqual(self.category()['result']['rate'],.6)
        self.load(files)
        self.assertEqual(len(self.category()['projects']),2)
        self.assertEqual(self.category()['projects'][0]['version_count'],1)

    def test_three_projects_and_internal_duplicates(self):
        self.load([file('A',[('X','新开件'),('A','借用件'),('A','借用件')]),file('B',[('X','借用件')]),file('C',[('X','借用件')])])
        r=self.category()['result']
        self.assertEqual((r['total'],r['duplicate_count'],r['borrowed'],r['denominator']),(5,1,2,4))
        self.assertEqual(r['rate'],.75)
        self.assertTrue(self.category()['projects'][0]['warnings'])

    def test_update_delete_restore_purge(self):
        self.load([file('A',[('X','新开件')])]); pid=self.category()['projects'][0]['id']
        self.load([file('A',[('Y','借用件')])]); self.assertEqual(self.category()['projects'][0]['version_count'],2)
        self.assertEqual(self.category()['result']['total'],1)
        self.store.mutate('trash',dict(kind='project',id=pid)); self.assertIsNone(self.category()['result']['rate'])
        self.store.mutate('restore',dict(kind='project',id=pid)); self.assertEqual(self.category()['result']['rate'],1)
        self.store.mutate('trash',dict(kind='project',id=pid)); self.store.mutate('purge',dict(kind='project',id=pid))
        with self.store.db() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM versions').fetchone()[0],0)

    def test_category_isolation_and_restore(self):
        self.load([file('A',[('X','新开件')])]); self.store.mutate('category',{'name':'车架'})
        other=self.store.state()['categories'][1]['id']
        self.store.mutate('import',dict(category=other,files=[file('B',[('X','借用件')])]))
        self.assertEqual(self.category()['result']['duplicate_count'],0)
        self.store.mutate('trash',dict(kind='category',id=self.cid))
        self.assertEqual(len(self.store.state()['categories']),1)
        self.store.mutate('restore',dict(kind='category',id=self.cid))
        self.assertEqual(len(self.store.state()['categories']),2)

    def test_migration_and_corruption(self):
        self.load([file('A',[('001-00','借用件')])]); package=self.store.migration()
        with tempfile.TemporaryDirectory() as target:
            other=Store(target); other.restore_package(package)
            self.assertEqual(other.state(),self.store.state())
            with other.db() as db: self.assertTrue(db.execute('SELECT original FROM versions').fetchone()[0])
            bad=io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(package)) as source,zipfile.ZipFile(bad,'w') as dest:
                dest.writestr('reuse.sqlite3',source.read('reuse.sqlite3')+b'broken')
                dest.writestr('manifest.json',source.read('manifest.json'))
            with self.assertRaises(ValueError): other.restore_package(bad.getvalue())
            self.assertEqual(other.state(),self.store.state())

    def test_invalid_import_atomicity(self):
        self.load([file('A',[('X','借用件')])])
        with self.assertRaises(ValueError): self.load([file('B',[('Y','新开件')]),file('C',[('Z','错误')])])
        self.assertEqual(len(self.category()['projects']),1)
        preview=self.store.preview(dict(category=self.cid,files=[file('B',[('Y','新开件')])]))
        self.assertEqual(preview['files'][0]['count'],1)
        self.assertEqual(len(self.category()['projects']),1)
        self.assertTrue(self.store.report().startswith(b'PK'))

    def test_fifty_projects_recalculate(self):
        self.load([file(str(i),[('shared','借用件'),(str(i),'新开件')]) for i in range(50)])
        r=self.category()['result']; self.assertEqual((r['total'],r['duplicate_count'],r['borrowed'],r['denominator']),(100,1,0,99))

    def test_all_worksheets_contribute_to_one_project(self):
        workbook = Workbook()
        workbook.active.title = '说明'
        workbook.active.append(['项目说明'])
        for name, header_row in [('首批物料', 1), ('追加物料', 53)]:
            sheet = workbook.create_sheet(name)
            sheet.cell(header_row, 1, '子阶料号')
            sheet.cell(header_row, 2, '借用件/新开件')
            sheet.append(['共享料号', '借用件'])
        workbook.create_sheet('空表')
        data = io.BytesIO()
        workbook.save(data)
        upload = {'name': '多标签.xlsx', 'content': base64.b64encode(data.getvalue()).decode()}
        preview = self.load([upload])
        self.assertEqual(preview['files'][0]['sheets'], {'首批物料': 1, '追加物料': 1})
        self.assertTrue(any('说明' in warning for warning in preview['files'][0]['warnings']))
        result = self.category()['result']
        self.assertEqual((result['total'], result['borrowed'], result['duplicate_count']), (2, 2, 0))

    def test_subcategories_isolate_calculation_and_average_equally(self):
        self.load([file('旧项目', [('X', '借用件')])])
        original = self.category()['projects'][0]
        for name in ('前排', '后排', '空分类'):
            self.store.mutate('category', {'name': name, 'parent_id': self.cid})
        categories = {c['name']: c for c in self.store.state()['categories']}
        self.assertEqual(categories['未分组']['projects'][0]['version_id'], original['version_id'])
        self.store.mutate('move_project', {'id': original['id'], 'category': categories['前排']['id']})
        self.store.mutate('import', {
            'category': categories['后排']['id'],
            'files': [file('后排项目', [('X', '新开件'), ('Y', '新开件'), ('Z', '新开件')])],
        })
        categories = {c['name']: c for c in self.store.state()['categories']}
        self.assertEqual(categories['前排']['result']['duplicate_count'], 0)
        self.assertEqual(categories['后排']['result']['duplicate_count'], 0)
        self.assertEqual(categories['座椅']['result'], {'rate': .5, 'total': 4, 'rated_children': 2})
        with self.assertRaisesRegex(ValueError, '最小分类'):
            self.load([file('直属项目', [('X', '新开件')])])
        with tempfile.TemporaryDirectory() as target:
            other = Store(target)
            other.restore_package(self.store.migration())
            self.assertEqual(other.state(), self.store.state())
        self.store.mutate('trash', {'kind': 'category', 'id': self.cid})
        self.assertFalse(self.store.state()['categories'])
        self.store.mutate('restore', {'kind': 'category', 'id': self.cid})
        self.assertEqual(self.category()['result']['rate'], .5)

    def test_upgrade_retains_existing_database_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reuse.sqlite3'
            db = sqlite3.connect(path)
            try:
                db.executescript(SCHEMA)
                db.execute("INSERT INTO categories(id,name) VALUES(7,'座椅')")
                db.execute("INSERT INTO projects(id,category_id,name) VALUES(8,7,'旧项目')")
                rows = json.dumps([{'code': 'X', 'kind': '借用件', 'sheet': 'Sheet', 'row': 2}])
                db.execute("INSERT INTO versions VALUES(9,8,'旧表.xlsx','digest','2026-01-01',?,?,'[]')", (b'original', rows))
                db.commit()
            finally:
                db.close()
            upgraded = Store(directory)
            category = upgraded.state()['categories'][0]
            self.assertEqual((category['id'], category['projects'][0]['version_id'], category['result']['rate']), (7, 9, 1))
            with upgraded.db() as db:
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

if __name__=='__main__': unittest.main(verbosity=2)
