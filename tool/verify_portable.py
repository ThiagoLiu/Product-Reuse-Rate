"""Smoke test the Windows launcher and the local HTTP workflow in a disposable copy."""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time
from urllib.request import Request, build_opener, ProxyHandler
from zipfile import ZipFile

from openpyxl import load_workbook

root=Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--archive', type=Path)
archive = parser.parse_args().archive
http=build_opener(ProxyHandler({}))
with TemporaryDirectory(dir=root,prefix='verify-') as tmp:
    if archive:
        with ZipFile(archive) as package:
            assert package.testzip() is None
            assert all('data' not in Path(name).parts for name in package.namelist())
            package.extractall(tmp)
        dest = Path(tmp)/'ProductReuseRate'
    else:
        dest=Path(tmp)/'验证便携版'
        shutil.copytree(root/'便携版',dest,ignore=shutil.ignore_patterns('data'))
    env=dict(os.environ,PRODUCT_REUSE_RATE_NO_BROWSER='1')
    proc=subprocess.Popen([str(dest/'ProductReuseRate.exe')],env=env)
    address=dest/'data'/'running.url.txt'
    token=None
    try:
        for _ in range(100):
            if address.exists() and address.stat().st_size: break
            if proc.poll() is not None: raise RuntimeError('launcher quit early')
            time.sleep(.1)
        url=address.read_text(encoding='ascii')
        html=http.open(url,timeout=5).read().decode()
        token=re.search(r'name="app-token" content="([^\"]+)"',html)[1]
        assert 'V1.0.0' in html and '新建小分类' in html
        assert http.open(url+'/app.js',timeout=5).status==200
        assert http.open(url+'/style.css',timeout=5).status==200
        assert http.open(url+'/byd-logo.svg',timeout=5).headers.get_content_type()=='image/svg+xml'
        def request(path,data=None):
            headers={'X-App-Token':token,'Content-Type':'application/json'}
            req=Request(url+'/api/'+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
            with http.open(req,timeout=8) as response: return response.read()
        def api(path,data=None): return json.loads(request(path,data))
        assert api('state') == {'categories': [], 'trash': []}
        api('category',{'name':'座椅'})
        cid=api('state')['categories'][0]['id']
        source=root.parent.parent/'座椅'
        files=[{'name':p.name,'content':base64.b64encode(p.read_bytes()).decode()} for p in sorted(source.glob('*.xlsx'))]
        payload=dict(category=cid,files=files)
        assert len(api('preview',payload)['files'])==2
        api('import',payload)
        c=api('state')['categories'][0]; assert c['result']['rate']==.6
        assert c['result']['duplicate_count']==2 and c['result']['borrowed']==22
        pid=c['projects'][0]['id']
        version=api(f'versions?id={pid}')[0]['id']
        assert request(f'original?id={version}')==base64.b64decode(files[0]['content'])
        wb=load_workbook(io.BytesIO(request('report')),read_only=True)
        try: assert wb['分类排名']['I2'].value==.6
        finally: wb.close()
        api('category', {'name': '前排', 'parent_id': cid})
        categories = {item['name']: item for item in api('state')['categories']}
        assert categories['座椅']['result']['rate'] == .6
        assert categories['未分组']['projects'][0]['id'] == pid
        migration=request('export')
        api('trash_many',{'category':categories['未分组']['id'],'ids':[p['id'] for p in c['projects']]})
        assert api('state')['categories'][0]['result']['rate'] is None
        api('migration',{'content':base64.b64encode(migration).decode()})
        restored=api('state')
        assert restored['categories'][0]['result']['rate']==.6
        second=subprocess.run([str(dest/'ProductReuseRate.exe')],env=env,timeout=5)
        assert second.returncode==0 and address.read_text(encoding='ascii')==url
        api('tab-open', {'tab': 'package-test'})
        api('tab-close', {'tab': 'package-test'})
        assert proc.wait(timeout=12)==0 and not address.exists()
        print('Portable launcher, offline logo, sample 60%, subcategories, migration and automatic exit: PASS')
    finally:
        if token and proc.poll() is None:
            api('shutdown',{})
            assert proc.wait(timeout=8)==0
        elif proc.poll() is None:
            proc.terminate(); proc.wait(timeout=5)
