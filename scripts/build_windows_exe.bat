@echo off
setlocal
cd /d "%~dp0\.."
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo Python 3 not found. Install Python 3.10 or newer from python.org.
  exit /b 1
)

%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo Python 3.10 or newer is required.
  exit /b 1
)

if not exist .venv (
  %PYTHON_CMD% -m venv .venv
)

call .venv\Scripts\activate.bat
if errorlevel 1 exit /b 1
python -m pip install --upgrade pip
if errorlevel 1 exit /b 1
python -m pip install -e ".[desktop]"
if errorlevel 1 exit /b 1
python -m unittest discover -s tests -q
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name StrategicMultiAgentGame ^
  --collect-data strategic_game ^
  desktop_app.py
if errorlevel 1 exit /b 1

echo.
echo Build complete: dist\StrategicMultiAgentGame.exe
echo You can move the EXE to another folder and run it directly.
endlocal
