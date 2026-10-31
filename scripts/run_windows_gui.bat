@echo off
setlocal
cd /d "%~dp0\.."

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo Python 3 not found. Install Python 3.10 or newer from python.org.
  pause
  exit /b 1
)

if not exist .venv (
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto :error
)
call .venv\Scripts\activate.bat
if errorlevel 1 goto :error
python -m pip install -e .
if errorlevel 1 goto :error
python -m strategic_game.gui
exit /b %errorlevel%

:error
echo Failed to prepare or start the desktop application.
pause
exit /b 1
