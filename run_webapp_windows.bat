@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.11 or newer from python.org.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo PromptFit Studio is starting at http://localhost:8000
echo Leave this window open while you use the app.
echo.
python -m uvicorn webapp.app:app --host 0.0.0.0 --port 8000

endlocal
