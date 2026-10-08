@echo off
rem One-click start on Windows: creates the virtualenv on first run, installs, opens the browser.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.11-3.13 is required: https://www.python.org/downloads/ & pause & exit /b 1)
if not exist .venv\Scripts\python.exe (
  echo First run: creating .venv and installing packages. This takes a few minutes.
  python -m venv .venv || (pause & exit /b 1)
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt || (pause & exit /b 1)
)
echo The first background removal downloads a ~180 MB model once; that upload takes longer.
start "" http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn main:app --port 8000
pause
