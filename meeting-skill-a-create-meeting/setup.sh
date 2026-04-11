#!/usr/bin/env bash
set -e

echo "[1/3] Creating virtual environment if needed..."
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

echo "[2/3] Activating virtual environment..."
source .venv/bin/activate

echo "[3/3] Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Done."
echo "Next:"
echo "  1. cp env-example.txt .env"
echo "  2. fill in your environment variables"
echo "  3. source .venv/bin/activate"
echo "  4. python main.py --query '明天下午4点开灵巧手项目会议' --organizer mayidan"