#!/bin/bash
set -e
echo "[Skill-C] 开始安装依赖..."
python3 --version || { echo "错误：未找到 python3"; exit 1; }
pip3 install -r requirements.txt
echo "[Skill-C] 依赖安装完成。"