"""
window_calculator.py
计算 Gitea 活动扫描的时间窗口。

规则（来自方案书）：
- 循环会议（有 series_id）：取同 series_id 上一次会议的 scheduled_time 到现在
- 临时会议（无 series_id）：取同仓库最近一次任意会议的 scheduled_time 到现在
- 找不到历史记录：取 now - 7天 到 now
"""

import datetime
import pytz
import yaml

from scripts.common import gitea_get, now_beijing

TZ = pytz.timezone("Asia/Shanghai")


def get_scan_window(repo_full_name: str, meeting_dir: str, meta: dict) -> tuple[datetime.datetime, datetime.datetime]:
    """
    返回 (since, until) 两个带时区的 datetime，均为北京时间。
    since: 扫描起点
    until: 扫描终点（= 当前时间）
    """
    now = now_beijing()
    fallback_since = now - datetime.timedelta(days=7)

    series_id = meta.get("series_id")
    owner, repo = repo_full_name.split("/", 1)

    # 列出所有会议目录
    contents = gitea_get(f"/api/v1/repos/{owner}/{repo}/contents/meetings")
    if not contents or not isinstance(contents, list):
        return fallback_since, now

    all_dirs = sorted(
        [item["name"] for item in contents
         if item.get("type") == "dir" and item["name"] != "archive"],
        reverse=True
    )

    # 排除当前会议自身
    other_dirs = [d for d in all_dirs if d != meeting_dir]

    if series_id:
        # 循环会议：找同 series_id 的上一次
        since = _find_last_series_time(repo_full_name, other_dirs, series_id, now)
    else:
        # 临时会议：找同仓库最近一次任意会议
        since = _find_last_any_meeting_time(repo_full_name, other_dirs, now)

    return since or fallback_since, now


def _find_last_series_time(repo_full_name: str, dirs: list[str],
                           series_id: str, now: datetime.datetime) -> datetime.datetime | None:
    """在历史会议目录中找同 series_id 最近一次的 scheduled_time。"""
    from scripts.gitea_ops import read_file
    for d in dirs:
        content, _ = read_file(repo_full_name, f"meetings/{d}/meta.yaml")
        if not content:
            continue
        try:
            m = yaml.safe_load(content)
            if m.get("series_id") == series_id:
                return _parse_scheduled_time(m.get("scheduled_time"), now)
        except Exception:
            continue
    return None


def _find_last_any_meeting_time(repo_full_name: str, dirs: list[str],
                                now: datetime.datetime) -> datetime.datetime | None:
    """在历史会议目录中找最近一次任意会议的 scheduled_time。"""
    from scripts.gitea_ops import read_file
    for d in dirs:
        content, _ = read_file(repo_full_name, f"meetings/{d}/meta.yaml")
        if not content:
            continue
        try:
            m = yaml.safe_load(content)
            t = _parse_scheduled_time(m.get("scheduled_time"), now)
            if t:
                return t
        except Exception:
            continue
    return None


def _parse_scheduled_time(time_str: str | None, now: datetime.datetime) -> datetime.datetime | None:
    """把 meta.yaml 中的 scheduled_time 字符串解析为带时区 datetime。"""
    if not time_str:
        return None
    try:
        import dateutil.parser
        dt = dateutil.parser.parse(str(time_str))
        if dt.tzinfo is None:
            dt = TZ.localize(dt)
        else:
            dt = dt.astimezone(TZ)
        # 只接受过去的时间作为 since
        if dt < now:
            return dt
        return None
    except Exception:
        return None