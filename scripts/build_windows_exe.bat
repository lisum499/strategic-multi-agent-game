@echo off
setlocal
cd /d "%~dp0\.."

if not exist .venv (
  py -3.10 -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e ".[desktop]"
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name StrategicMultiAgentGame ^
  --collect-data strategic_game ^
  desktop_app.py

echo.
echo Build complete: dist\StrategicMultiAgentGame.exe
endlocal
