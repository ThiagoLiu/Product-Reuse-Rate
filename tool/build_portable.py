"""Build a portable Windows folder from an installed CPython + openpyxl."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT=Path(__file__).resolve().parent
PYTHON=Path(sys.base_prefix)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=ROOT/'便携版')
OUT = parser.parse_args().output.resolve()
RUNTIME=OUT/'runtime'
RUNTIME.mkdir(parents=True,exist_ok=True)
for filename in ('python.exe','python3.dll','python314.dll','vcruntime140.dll','vcruntime140_1.dll','LICENSE.txt'):
    shutil.copy2(PYTHON/filename,RUNTIME/filename)
for path in (PYTHON/'DLLs').iterdir():
    if path.suffix.lower() in ('.dll','.pyd'):
        shutil.copy2(path,RUNTIME/path.name)
stdlib=PYTHON/'Lib'
excluded={'site-packages','test','tests','idlelib','tkinter','turtledemo','ensurepip','__pycache__','venv'}
with ZipFile(RUNTIME/'python314.zip','w',ZIP_DEFLATED) as z:
    for path in stdlib.rglob('*.py'):
        rel=path.relative_to(stdlib)
        if not excluded.intersection(rel.parts): z.write(path,rel.as_posix())
for package in ('openpyxl','et_xmlfile'):
    spec=importlib.util.find_spec(package)
    if not spec or not spec.submodule_search_locations:
        raise RuntimeError(f'缺少打包依赖：{package}')
    source=Path(next(iter(spec.submodule_search_locations)))
    shutil.copytree(source,RUNTIME/'packages'/package,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    for metadata in source.parent.glob(package+'-*.dist-info'):
        shutil.copytree(metadata,RUNTIME/'packages'/metadata.name,dirs_exist_ok=True)
(RUNTIME/'python314._pth').write_text('python314.zip\n.\npackages\n',encoding='ascii')
shutil.copy2(ROOT/'app.py',OUT/'app.py')
shutil.copy2(ROOT/'portable-readme.txt',OUT/'使用说明.txt')
shutil.copytree(ROOT/'web',OUT/'web',dirs_exist_ok=True)
compiler=Path(os.environ['SystemRoot'])/'Microsoft.NET'/'Framework64'/'v4.0.30319'/'csc.exe'
subprocess.run([str(compiler),'/nologo','/target:winexe','/reference:System.Windows.Forms.dll',f'/out:{OUT / "ProductReuseRate.exe"}',str(ROOT/'Launcher.cs')],check=True)
print(OUT)
