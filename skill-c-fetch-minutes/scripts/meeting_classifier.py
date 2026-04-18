"""
meeting_classifier.py
遍历所有受管仓库的 meetings/*/meta.yaml，
把当前时刻需要处理的会议分为三类返回：

A 类：status == brief-sent，且会议已结束
      → 检查是否结束，已结束则推进到 waiting-transcript

B 类：status == waiting-transcript
      → 检查超时（>TIMEOUT分钟），未超时则尝试拉转录

C 类：status == transcript-failed，且组织者已手动上传 transcript.md
      → 直接进入解析阶段

每类返回列表，每个元素：
{
  "repo":        str,
  "meeting_dir": str,
  "meta":        dict,
  "sha":         str   # meta.yaml 当前 sha，用于后续更新
}
"""

import os
import datetime
import pytz
import yaml
import dateutil.parser

from scripts.common   import get_managed_repos, now_beijing, gitea_get
from scripts.gitea_ops import read_file

TZ = pytz.timezone("Asia/Shanghai")
TIMEOUT_MINUTES = int(os.environ.get("TRANSCRIPT_TIMEOUT_MINUTES", "60"))


def classify_meetings() -> tuple[list, list, list]:
    """
    返回 (class_a, class_b, class_c) 三个列表。
    """
    repos = get_managed_repos()
    class_a, class_b, class_c = [], [], []
    now = now_beijing()

    for repo in repos:
        owner, reponame = repo.split("/", 1)
        contents = gitea_get(
            f"/api/v1/repos/{owner}/{reponame}/contents/meetings"
        )
        if not contents or not isinstance(contents, list):
            continue

        meeting_dirs = [
            item["name"] for item in contents
            if item.get("type") == "dir" and item["name"] != "archive"
        ]

        for meeting_dir in meeting_dirs:
            meta, sha = _load_meta(repo, meeting_dir)
            if not meta or not sha:
                continue

            status = meta.get("status", "")
            item = {
                "repo":        repo,
                "meeting_dir": meeting_dir,
                "meta":        meta,
                "sha":         sha
            }

            # ── A 类：brief-sent 且已结束 ─────────────────
            if status == "brief-sent":
                if _is_meeting_ended(meta, now):
                    class_a.append(item)

            # ── B 类：waiting-transcript ──────────────────
            elif status == "waiting-transcript":
                class_b.append(item)

            # ── C 类：transcript-failed 且已手动上传 ──────
            elif status == "transcript-failed":
                transcript_exists = _file_exists(
                    repo, f"meetings/{meeting_dir}/transcript.md"
                )
                if transcript_exists:
                    class_c.append(item)

    return class_a, class_b, class_c


def is_timeout(meta: dict, now: datetime.datetime) -> bool:
    """
    判断 B 类会议是否已超时（从会议结束时间起超过 TIMEOUT_MINUTES 分钟）。
    """
    end_time_str = meta.get("end_time") or meta.get("scheduled_time", "")
    if not end_time_str:
        return False
    try:
        end_dt = dateutil.parser.parse(str(end_time_str))
        if end_dt.tzinfo is None:
            end_dt = TZ.localize(end_dt)
        else:
            end_dt = end_dt.astimezone(TZ)
        elapsed = (now - end_dt).total_seconds() / 60
        return elapsed > TIMEOUT_MINUTES
    except Exception:
        return False


# ── 内部工具 ──────────────────────────────────────────────

def _load_meta(repo: str, meeting_dir: str) -> tuple[dict | None, str | None]:
    content, sha = read_file(repo, f"meetings/{meeting_dir}/meta.yaml")
    if not content:
        return None, None
    try:
        return yaml.safe_load(content), sha
    except Exception:
        return None, None


def _is_meeting_ended(meta: dict, now: datetime.datetime) -> bool:
    """判断会议结束时间是否已过。"""
    end_str = meta.get("end_time") or meta.get("scheduled_time", "")
    if not end_str:
        return False
    try:
        dt = dateutil.parser.parse(str(end_str))
        if dt.tzinfo is None:
            dt = TZ.localize(dt)
        else:
            dt = dt.astimezone(TZ)
        return now > dt
    except Exception:
        return False


def _file_exists(repo: str, path: str) -> bool:
    content, _ = read_file(repo, path)
    return content is not None