# -*- coding: utf-8 -*-

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytz
import yaml
from dateutil import parser as dt_parser


class SkillError(Exception):
    def __init__(self, message: str, stage: str = "unknown"):
        super().__init__(message)
        self.stage = stage


def load_dotenv_if_exists():
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(root_dir, ".env")
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if key and key not in os.environ:
                os.environ[key] = val


def load_settings() -> Dict[str, Any]:
    load_dotenv_if_exists()

    def getenv(key: str, default: str = "") -> str:
        return os.getenv(key, default).strip()

    project_repo_map_raw = getenv("PROJECT_REPO_MAP_JSON", "{}")
    try:
        project_repo_map = json.loads(project_repo_map_raw)
    except json.JSONDecodeError:
        project_repo_map = {}

    smtp_use_ssl = getenv("SMTP_USE_SSL", "true").lower() in ("1", "true", "yes", "y")

    return {
        "TZ": getenv("TZ", "Asia/Shanghai"),
        "DEFAULT_MEETING_DURATION_MINUTES": int(getenv("DEFAULT_MEETING_DURATION_MINUTES", "60")),
        "GITEA_BASE_URL": getenv("GITEA_BASE_URL"),
        "GITEA_TOKEN_BOT": getenv("GITEA_TOKEN_BOT"),
        "GITEA_DEFAULT_BRANCH": getenv("GITEA_DEFAULT_BRANCH", "main"),
        "AIFUSION_META_REPO": getenv("AIFUSION_META_REPO"),
        "ADVISOR_GITEA_USERNAME": getenv("ADVISOR_GITEA_USERNAME", ""),
        "PROJECT_REPO_MAP": project_repo_map,
        "DEFAULT_ATTENDEES": parse_attendees(getenv("DEFAULT_ATTENDEES", "")),
        "ANTHROPIC_BASE_URL": getenv("ANTHROPIC_BASE_URL", "https://api.minimax.io/anthropic"),
        "ANTHROPIC_API_KEY": getenv("ANTHROPIC_API_KEY"),
        "MINIMAX_MODEL": getenv("MINIMAX_MODEL", "MiniMax-M2.7"),
        "MINIMAX_MAX_TOKENS": int(getenv("MINIMAX_MAX_TOKENS", "1400")),
        "SMTP_HOST": getenv("SMTP_HOST"),
        "SMTP_PORT": int(getenv("SMTP_PORT", "465")),
        "SMTP_USE_SSL": smtp_use_ssl,
        "SMTP_USER": getenv("SMTP_USER"),
        "SMTP_PASSWORD": getenv("SMTP_PASSWORD"),
        "SMTP_SENDER_NAME": getenv("SMTP_SENDER_NAME", "AIFusionBot"),
        "TENCENT_MEETING_MODE": getenv("TENCENT_MEETING_MODE", "mock"),
        "TENCENT_MEETING_BRIDGE_URL": getenv("TENCENT_MEETING_BRIDGE_URL"),
        "TENCENT_MEETING_CANCEL_URL": getenv("TENCENT_MEETING_CANCEL_URL"),
        "TENCENT_MEETING_BRIDGE_TOKEN": getenv("TENCENT_MEETING_BRIDGE_TOKEN"),
        "TENCENT_MEETING_COMMAND": getenv("TENCENT_MEETING_COMMAND"),
        "TENCENT_MEETING_DEFAULT_PASSWORD": getenv("TENCENT_MEETING_DEFAULT_PASSWORD"),
    }


def now_local(tz_name: str = "Asia/Shanghai") -> datetime:
    tz = pytz.timezone(tz_name)
    return datetime.now(tz)


def ensure_tz(dt: datetime, tz_name: str) -> datetime:
    tz = pytz.timezone(tz_name)
    if dt.tzinfo is None:
        return tz.localize(dt)
    return dt.astimezone(tz)


def parse_datetime_with_tz(text: str, tz_name: str) -> datetime:
    dt = dt_parser.parse(text)
    return ensure_tz(dt, tz_name)


def parse_attendees(text: str) -> List[str]:
    if not text:
        return []
    parts = re.split(r"[,\n;，；\s]+", text.strip())
    result = []
    for p in parts:
        p = p.strip()
        if p and p not in result:
            result.append(p)
    return result


def safe_json_dumps(data: Any, **kwargs) -> str:
    return json.dumps(data, **kwargs, default=str)


def make_meeting_dir_name(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d-%H%M")


def yaml_dump(data: Dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def build_meta_yaml_text(
    meeting_info: Dict[str, Any],
    topic: str,
    scheduled_time: datetime,
    duration_minutes: int,
    meeting_type: str,
    series_id: Optional[str],
    meeting_category: str,
    repo: str,
    organizer: str,
    attendees: List[str],
) -> str:
    payload = {
        "meeting_id": str(meeting_info["meeting_id"]),
        "meeting_code": str(meeting_info["meeting_code"]),
        "join_url": meeting_info["join_url"],
        "topic": topic,
        "scheduled_time": scheduled_time.isoformat(),
        "duration_minutes": int(duration_minutes),
        "type": meeting_type,
        "meeting_category": meeting_category,
        "repo": repo,
        "organizer": organizer,
        "attendees": attendees,
        "status": "scheduled",
    }
    if series_id:
        payload["series_id"] = series_id
    return yaml_dump(payload)


def build_agenda_markdown(
    topic: str,
    scheduled_time: datetime,
    organizer: str,
    attendees: List[str],
    repo: str,
    join_url: str,
    meeting_code: str,
    previous_summary: Dict[str, Any],
) -> str:
    attendee_text = ", ".join(attendees) if attendees else "暂无"
    summary_lines = previous_summary.get("summary_bullets", [])
    previous_source = previous_summary.get("source", "暂无参考记录")

    if summary_lines:
        summary_md = "\n".join([f"- {x}" for x in summary_lines])
    else:
        summary_md = "暂无参考记录"

    return f"""## 会议基本信息

- 主题：{topic}
- 时间：{scheduled_time.strftime("%Y-%m-%d %H:%M %Z")}
- 腾讯会议链接：{join_url}
- 会议号：{meeting_code}
- 与会人员：{attendee_text}
- 组织者：{organizer}
- 所属项目：{repo}

## 本次议程

（请组织者在此填写）

## 上次会议内容回顾

（来源：{previous_source}）

{summary_md}
"""


def normalize_project_key(text: str) -> str:
    return re.sub(r"\s+", "", text or "").strip().lower()


def detect_repo_from_text(text: str, settings: Dict[str, Any]) -> Optional[str]:
    if not text:
        return None

    normalized = normalize_project_key(text)
    project_repo_map = settings.get("PROJECT_REPO_MAP", {})

    # 精确匹配
    for k, v in project_repo_map.items():
        if normalize_project_key(k) == normalized:
            return v

    # 包含匹配
    for k, v in project_repo_map.items():
        if normalize_project_key(k) in normalized or normalized in normalize_project_key(k):
            return v

    # 直接写了 full repo
    if "/" in text and len(text.split("/", 1)) == 2:
        return text.strip()

    if "跨项目" in text:
        return settings.get("AIFUSION_META_REPO")

    return None