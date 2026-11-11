@echo off
setlocal
cd /d "%~dp0tool" || exit /b 1
for /d %%D in (*) do (
  if exist "%%D\runtime\python.exe" if exist "%%D\ProductReuseRate.exe" (
    "%%D\runtime\python.exe" -B "%CD%\app.py" --data-dir "%%~fD\data" %*
    goto :done
  )
)
python -B "%CD%\app.py" %*
:done
if errorlevel 1 pause
