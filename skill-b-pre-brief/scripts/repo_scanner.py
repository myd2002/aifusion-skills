"""
repo_scanner.py
遍历所有受管仓库，扫描 meetings/*/meta.yaml，
筛选出需要发送会前简报的会议：
  - status == scheduled
  - scheduled_time 在 [now+30min, now+4h] 内
  - 不含 rescheduled_from 字段（改期后的旧目录跳过）

对外暴露：
  scan_pending_briefs() -> list[dict]
  每个 dict 包含: repo, meeting_dir, meta
"""

import datetime
import pytz
import yaml

from scripts.common import get_managed_repos, now_beijing, gitea_get
from scripts.gitea_ops import read_file

TZ = pytz.timezone("Asia/Shanghai")

BRIEF_WINDOW_MIN =  30   # 分钟：最早提前多久发简报
BRIEF_WINDOW_MAX = 240   # 分钟：最晚提前多久发简报（4小时）


def scan_pending_briefs() -> list[dict]:
    """
    扫描所有受管仓库，返回需要立即生成并发送会前简报的会议列表。
    每个元素：{"repo": str, "meeting_dir": str, "meta": dict}
    """
    now = now_beijing()
    window_start = now + datetime.timedelta(minutes=BRIEF_WINDOW_MIN)
    window_end   = now + datetime.timedelta(minutes=BRIEF_WINDOW_MAX)

    repos = get_managed_repos()
    pending = []

    for repo in repos:
        owner, reponame = repo.split("/", 1)
        contents = gitea_get(f"/api/v1/repos/{owner}/{reponame}/contents/meetings")
        if not contents or not isinstance(contents, list):
            continue

        meeting_dirs = [
            item["name"] for item in contents
            if item.get("type") == "dir" and item["name"] != "archive"
        ]

        for meeting_dir in meeting_dirs:
            meta, _ = _load_meta(repo, meeting_dir)
            if not meta:
                continue

            # 过滤条件 1：status 必须是 scheduled
            if meta.get("status") != "scheduled":
                continue

            # 过滤条件 2：不能是改期后的新目录（rescheduled_from 字段存在说明这个目录是改期后新建的，
            # 按方案书逻辑应直接置 brief-sent 跳过，不发邮件）
            if meta.get("rescheduled_from"):
                _mark_brief_sent_skip(repo, meeting_dir, meta)
                continue

            # 过滤条件 3：scheduled_time 在窗口内
            scheduled_dt = _parse_scheduled_time(meta.get("scheduled_time"))
            if not scheduled_dt:
                continue

            if window_start <= scheduled_dt <= window_end:
                pending.append({
                    "repo":        repo,
                    "meeting_dir": meeting_dir,
                    "meta":        meta,
                    "scheduled_dt": scheduled_dt
                })

    return pending


def _load_meta(repo: str, meeting_dir: str) -> tuple[dict | None, str | None]:
    content, sha = read_file(repo, f"meetings/{meeting_dir}/meta.yaml")
    if not content:
        return None, None
    try:
        return yaml.safe_load(content), sha
    except Exception:
        return None, None


def _parse_scheduled_time(time_str) -> datetime.datetime | None:
    if not time_str:
        return None
    try:
        import dateutil.parser
        dt = dateutil.parser.parse(str(time_str))
        if dt.tzinfo is None:
            dt = TZ.localize(dt)
        else:
            dt = dt.astimezone(TZ)
        return dt
    except Exception:
        return None


def _mark_brief_sent_skip(repo: str, meeting_dir: str, meta: dict):
    """
    对含 rescheduled_from 的会议，直接置 brief-sent，不发邮件。
    （方案书 3.3 特殊处理：改期后的新目录避免重复发简报）
    """
    from scripts.gitea_ops import read_file, update_file
    import yaml as _yaml

    path = f"meetings/{meeting_dir}/meta.yaml"
    content, sha = read_file(repo, path)
    if not content or not sha:
        return
    try:
        m = _yaml.safe_load(content)
        m["status"] = "brief-sent"
        new_content = _yaml.dump(m, allow_unicode=True, default_flow_style=False)
        update_file(repo, path, new_content, sha,
                    "chore: skip brief for rescheduled meeting")
    except Exception as e:
        print(f"[repo_scanner] _mark_brief_sent_skip 失败 {repo}/{meeting_dir}: {e}")