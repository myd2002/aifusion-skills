"""
gitea_state.py
汇总所有受管仓库中处于活跃状态（scheduled / brief-sent）的会议。

对外暴露：
  collect_gitea_meetings() -> list[dict]

每个元素结构：
{
  "repo":           str,   # owner/repo
  "meeting_dir":    str,   # YYYY-MM-DD-HHMM
  "meta":           dict,  # meta.yaml 完整内容
  "sha":            str,   # meta.yaml 当前 sha
  "meeting_id":     str,   # 腾讯会议 ID（可能为空）
  "scheduled_time": str,   # YYYY-MM-DD HH:MM（北京时间）
  "scheduled_ts":   int    # Unix 时间戳，解析失败为 0
}
"""

import datetime
import pytz
import yaml
import dateutil.parser

from scripts.common    import get_managed_repos, gitea_get
from scripts.gitea_ops import read_file

TZ = pytz.timezone("Asia/Shanghai")

ACTIVE_STATUSES = {"scheduled", "brief-sent"}


def collect_gitea_meetings() -> list[dict]:
    """
    遍历所有受管仓库，返回所有活跃状态会议的元信息列表。
    """
    repos  = get_managed_repos()
    result = []

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
            if status not in ACTIVE_STATUSES:
                continue

            scheduled_ts, scheduled_str = _parse_time(
                meta.get("scheduled_time", "")
            )

            result.append({
                "repo":           repo,
                "meeting_dir":    meeting_dir,
                "meta":           meta,
                "sha":            sha,
                "meeting_id":     meta.get("meeting_id", ""),
                "scheduled_time": scheduled_str,
                "scheduled_ts":   scheduled_ts
            })

    print(f"[gitea_state] 汇总到 {len(result)} 个活跃会议")
    return result


def collect_archivable_meetings() -> list[dict]:
    """
    汇总所有受管仓库中 status ∈ {cancelled, rescheduled} 且
    超过 30 天的会议目录，供归档清理使用。
    """
    import datetime
    repos  = get_managed_repos()
    now    = datetime.datetime.now(TZ)
    cutoff = now - datetime.timedelta(days=30)
    result = []

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
            if status not in ("cancelled", "rescheduled"):
                continue

            scheduled_ts, _ = _parse_time(meta.get("scheduled_time", ""))
            if scheduled_ts == 0:
                continue

            meeting_dt = datetime.datetime.fromtimestamp(scheduled_ts, tz=TZ)
            if meeting_dt < cutoff:
                result.append({
                    "repo":        repo,
                    "meeting_dir": meeting_dir,
                    "meta":        meta,
                    "sha":         sha
                })

    print(f"[gitea_state] 找到 {len(result)} 个可归档会议")
    return result


# ── 内部工具 ──────────────────────────────────────────────

def _load_meta(repo: str, meeting_dir: str) -> tuple[dict | None, str | None]:
    content, sha = read_file(repo, f"meetings/{meeting_dir}/meta.yaml")
    if not content:
        return None, None
    try:
        return yaml.safe_load(content), sha
    except Exception:
        return None, None


def _parse_time(time_str: str) -> tuple[int, str]:
    """
    解析 scheduled_time 字符串，返回 (unix_ts, readable_str)。
    解析失败返回 (0, "")。
    """
    if not time_str:
        return 0, ""
    try:
        dt = dateutil.parser.parse(str(time_str))
        if dt.tzinfo is None:
            dt = TZ.localize(dt)
        else:
            dt = dt.astimezone(TZ)
        return int(dt.timestamp()), dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return 0, ""