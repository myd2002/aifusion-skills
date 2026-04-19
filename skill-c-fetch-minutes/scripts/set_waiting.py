#!/usr/bin/env python3
"""
Skill-C set-waiting：将会议状态 brief-sent → waiting-transcript。
记录 transcript_started_at 时间戳，初始化 transcript_poll_count = 0。

用法：
    python3 set_waiting.py \
      --repo "HKU-AIFusion/dexterous-hand" \
      --meeting-dir "2026-04-22-1500"
"""

import os
import sys
import json
import argparse
from datetime import datetime

import pytz
import yaml
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.config/skill-c-fetch-minutes/.env"))

sys.path.insert(0, os.path.dirname(__file__))
from gitea_utils import get_file_from_repo, update_file_in_repo
from log_utils import write_log

GITEA_BASE_URL = os.getenv("GITEA_BASE_URL", "")
GITEA_TOKEN    = os.getenv("GITEA_TOKEN_BOT", "")
META_REPO      = os.getenv("AIFUSION_META_REPO", "")
TZ             = pytz.timezone("Asia/Shanghai")


def main():
    parser = argparse.ArgumentParser(description="Skill-C set-waiting")
    parser.add_argument("--repo",         required=True)
    parser.add_argument("--meeting-dir",  required=True)
    args = parser.parse_args()

    owner, repo_name = args.repo.split("/", 1)
    meta_path = f"meetings/{args.meeting_dir}/meta.yaml"

    raw, sha = get_file_from_repo(owner, repo_name, meta_path, GITEA_TOKEN, GITEA_BASE_URL)
    if raw is None:
        _fail(f"meta.yaml 不存在：{meta_path}")

    try:
        meta = yaml.safe_load(raw) or {}
    except Exception as e:
        _fail(f"meta.yaml 解析失败：{e}")

    old_status = meta.get("status", "")

    # 幂等检查
    if old_status == "waiting-transcript":
        started_at = meta.get("transcript_started_at", datetime.now(TZ).isoformat())
        print(json.dumps({
            "success":              True,
            "idempotent":           True,
            "transcript_started_at": started_at,
            "message":              "已处于 waiting-transcript 状态",
        }, ensure_ascii=False, indent=2))
        return

    if old_status != "brief-sent":
        _fail(f"状态不符：期望 brief-sent，实际 {old_status}")

    now_str = datetime.now(TZ).isoformat()
    meta["status"]                = "waiting-transcript"
    meta["transcript_started_at"] = now_str
    meta["transcript_poll_count"] = 0

    new_content = yaml.dump(meta, allow_unicode=True, default_flow_style=False, sort_keys=False)
    try:
        update_file_in_repo(
            owner, repo_name, meta_path, new_content,
            f"chore(meeting): brief-sent → waiting-transcript [{args.meeting_dir}]",
            sha, GITEA_TOKEN, GITEA_BASE_URL,
        )
    except Exception as e:
        _fail(f"meta.yaml 更新失败：{e}")

    write_log({
        "ts":          datetime.now(TZ).isoformat(),
        "skill":       "skill-c",
        "repo":        args.repo,
        "meeting_dir": args.meeting_dir,
        "action":      "set-waiting-transcript",
        "status":      "ok",
        "details":     {"old_status": old_status},
    }, META_REPO, GITEA_TOKEN, GITEA_BASE_URL)

    print(json.dumps({
        "success":               True,
        "meeting_dir":           args.meeting_dir,
        "new_status":            "waiting-transcript",
        "transcript_started_at": now_str,
    }, ensure_ascii=False, indent=2))


def _fail(message):
    print(json.dumps({"success": False, "error": message}, ensure_ascii=False))
    sys.exit(1)


if __name__ == "__main__":
    main()
