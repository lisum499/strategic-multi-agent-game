@echo off
setlocal
cd /d "%~dp0\.."
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PYTHON_CMD="
if exist ".venv\Scripts\python.exe" set "PYTHON_CMD=.venv\Scripts\python.exe"
for %%V in (313 312 311 310) do if not defined PYTHON_CMD if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  where py >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
  echo Python 3 not found. Install Python 3.10 or newer from python.org.
  exit /b 1
)

%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo Selected Python is older than 3.10: %PYTHON_CMD%
  echo If .venv is old, delete it and run this script again.
  exit /b 1
)

if not exist .venv (
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 exit /b 1
)

.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -e ".[desktop]"
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m unittest discover -s tests -q
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -c "import webview"
if errorlevel 1 exit /b 1
where npm >nul 2>nul
if errorlevel 1 (
  echo Node.js and npm are required to build the React frontend.
  exit /b 1
)
pushd frontend
call npm install
if errorlevel 1 exit /b 1
call npm run build
if errorlevel 1 exit /b 1
popd
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name StrategicMultiAgentGame ^
  --collect-data strategic_game ^
  --collect-all webview ^
  --add-data "frontend\dist;web_dist" ^
  web_app.py
if errorlevel 1 exit /b 1

echo.
echo Build complete: dist\StrategicMultiAgentGame.exe
echo You can move the EXE to another folder and run it directly.
endlocal
