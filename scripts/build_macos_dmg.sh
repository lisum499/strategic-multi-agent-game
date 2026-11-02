#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ "$(uname -s)" != Darwin ]; then
  echo "Build the macOS application on macOS." >&2
  exit 1
fi
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[desktop]'
.venv/bin/python -m unittest discover -s tests -q
(cd frontend && npm ci && npm run build)
if [ -f .env ]; then
  .venv/bin/python -m PyInstaller --noconfirm --clean --windowed --name StrategicMultiAgentGame \
    --collect-data strategic_game --collect-all webview --add-data 'frontend/dist:web_dist' \
    --add-data '.env:.' web_app.py
else
  .venv/bin/python -m PyInstaller --noconfirm --clean --windowed --name StrategicMultiAgentGame \
    --collect-data strategic_game --collect-all webview --add-data 'frontend/dist:web_dist' web_app.py
fi
rm -f dist/StrategicMultiAgentGame.dmg
hdiutil create -volname StrategicMultiAgentGame -srcfolder dist/StrategicMultiAgentGame.app \
  -ov -format UDZO dist/StrategicMultiAgentGame.dmg
echo 'Build complete: dist/StrategicMultiAgentGame.dmg'
