@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
echo Creating a private Python environment...
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto failure
)
".venv\Scripts\python.exe" -m pip install -r requirements-browser.txt
if errorlevel 1 goto failure
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto failure
".venv\Scripts\python.exe" setup_login.py
if errorlevel 1 goto failure
echo Setup finished.
pause
exit /b 0
:failure
echo Setup did not finish. Keep this window open and check the error above.
pause
exit /b 1
