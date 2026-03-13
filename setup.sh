#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  InstaFlow – Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "❌  Python 3 is required. Install it from https://python.org"
  exit 1
fi

# Check ffmpeg
if ! command -v ffmpeg &>/dev/null; then
  echo "⚠️   ffmpeg not found. Install it:"
  echo "    macOS:  brew install ffmpeg"
  echo "    Ubuntu: sudo apt install ffmpeg"
  echo "    Windows: https://ffmpeg.org/download.html"
fi

# Create virtual environment
if [ ! -d "venv" ]; then
  echo "→  Creating virtual environment…"
  python3 -m venv venv
fi

source venv/bin/activate

echo "→  Installing dependencies…"
pip install -q -r requirements.txt

# Create .env if it doesn't exist
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "✅  Created .env file. Please fill in your API keys before running the app:"
  echo "    nano .env"
  echo ""
fi

# Create upload/processed dirs
mkdir -p static/uploads static/processed

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "  1. Edit .env with your API keys"
echo "  2. Run: source venv/bin/activate && python app.py"
echo "  3. Open: http://localhost:5000/admin"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
