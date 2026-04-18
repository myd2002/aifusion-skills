#!/bin/bash
set -e
echo "[Skill-D] 开始安装依赖..."
python3 --version || { echo "错误：未找到 python3"; exit 1; }
pip3 install -r requirements.txt
echo "[Skill-D] 依赖安装完成。"
echo "[Skill-D] Webhook 服务启动方式："
echo "  python main.py --mode webhook"
echo "[Skill-D] OpenClaw 对话调用方式："
echo "  python main.py --mode cli --repo owner/repo --meeting-dir YYYY-MM-DD-HHMM"