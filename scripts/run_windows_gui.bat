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
  pause
  exit /b 1
)

%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo Selected Python is older than 3.10: %PYTHON_CMD%
  echo If .venv is old, delete it and run this script again.
  pause
  exit /b 1
)

if not exist .venv (
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto :error
)
.venv\Scripts\python.exe -m pip install -e .
if errorlevel 1 goto :error
.venv\Scripts\python.exe -m strategic_game.gui
exit /b %errorlevel%

:error
echo Failed to prepare or start the desktop application.
pause
exit /b 1
