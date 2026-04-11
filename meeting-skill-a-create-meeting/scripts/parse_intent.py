# -*- coding: utf-8 -*-

import json
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional

from anthropic import Anthropic

from common import (
    SkillError,
    now_local,
    ensure_tz,
    detect_repo_from_text,
)


SYSTEM_PROMPT = """
你是会议创建意图解析器。你的唯一任务是把中文自然语言会议创建需求转成严格 JSON。
禁止输出 JSON 以外的任何内容。
若信息缺失，尽量根据上下文补齐；若无法补齐，字段填 null，不要编造。

输出字段：
{
  "topic": "会议主题",
  "scheduled_time": "ISO8601时间字符串，必须带时区，例如 2026-04-15T15:00:00+08:00",
  "duration_minutes": 60,
  "type": "recurring 或 ad-hoc",
  "series_id": null,
  "recurrence": {
    "freq": "weekly",
    "count": 10,
    "by_day": ["MO"]
  },
  "repo_name": "用户提到的项目名或仓库名，没提到则为 null",
  "attendees": ["mayidan", "sujinze"]
}

规则：
1. 若是一次性会议，type=ad-hoc，recurrence=null。
2. 若出现“每周一”“连续10周”等表达，type=recurring。
3. 若没明确时长，默认 60。
4. repo_name 保留用户原始表述，不要自己扩展成解释性句子。
5. attendees 只保留可能是用户名/人名的标识；没有就输出 []。
6. topic 尽量简洁，不要太长。
"""


def _extract_text_blocks(message) -> str:
    parts = []
    for block in message.content:
        if getattr(block, "type", "") == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


def _fallback_parse(
    query: str,
    organizer: str,
    attendees: List[str],
    repo_hint: str,
    topic_hint: str,
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    now = now_local(settings["TZ"])
    scheduled_time = now + timedelta(hours=1)

    topic = topic_hint or "项目会议"
    if "讨论" in query:
        idx = query.find("讨论")
        tail = query[idx:].strip("，。 ")
        if tail:
            topic = tail

    meeting_type = "recurring" if ("每周" in query or "连续" in query) else "ad-hoc"

    repo_name = repo_hint or None
    if not repo_name:
        if "跨项目" in query:
            repo_name = "跨项目会议"
        elif "灵巧手" in query:
            repo_name = "灵巧手项目"

    repo = detect_repo_from_text(repo_name or "", settings)

    final_attendees = list(attendees) if attendees else list(settings.get("DEFAULT_ATTENDEES", []))
    if organizer not in final_attendees:
        final_attendees.insert(0, organizer)

    return {
        "topic": topic,
        "scheduled_time": scheduled_time,
        "duration_minutes": settings["DEFAULT_MEETING_DURATION_MINUTES"],
        "type": meeting_type,
        "series_id": None,
        "recurrence": None,
        "repo_name": repo_name,
        "repo": repo,
        "attendees": final_attendees,
        "source": "fallback",
    }


def parse_user_intent(
    query: str,
    organizer: str,
    attendees: List[str],
    repo_hint: str,
    topic_hint: str,
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    api_key = settings.get("ANTHROPIC_API_KEY")
    base_url = settings.get("ANTHROPIC_BASE_URL")
    model = settings.get("MINIMAX_MODEL")
    max_tokens = settings.get("MINIMAX_MAX_TOKENS", 1400)

    if not api_key:
        return _fallback_parse(query, organizer, attendees, repo_hint, topic_hint, settings)

    try:
        client = Anthropic(api_key=api_key, base_url=base_url)
        user_prompt = f"""
当前时间：{now_local(settings["TZ"]).isoformat()}
组织者：{organizer}
已知与会者：{attendees}
repo_hint：{repo_hint or None}
topic_hint：{topic_hint or None}

用户原始指令：
{query}

请只输出 JSON。
"""

        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": user_prompt}],
                }
            ],
        )

        raw = _extract_text_blocks(message)
        parsed = json.loads(raw)

        repo_name = repo_hint or parsed.get("repo_name")
        repo = detect_repo_from_text(repo_name or "", settings)

        from dateutil import parser as dt_parser

        scheduled_time = dt_parser.parse(parsed["scheduled_time"])
        scheduled_time = ensure_tz(scheduled_time, settings["TZ"])

        final_attendees = parsed.get("attendees") or attendees or settings.get("DEFAULT_ATTENDEES", [])
        final_attendees = [x for x in final_attendees if x]
        if organizer not in final_attendees:
            final_attendees.insert(0, organizer)

        topic = topic_hint or parsed.get("topic") or "未命名会议"
        duration_minutes = int(parsed.get("duration_minutes") or settings["DEFAULT_MEETING_DURATION_MINUTES"])

        return {
            "topic": topic,
            "scheduled_time": scheduled_time,
            "duration_minutes": duration_minutes,
            "type": parsed.get("type", "ad-hoc"),
            "series_id": parsed.get("series_id"),
            "recurrence": parsed.get("recurrence"),
            "repo_name": repo_name,
            "repo": repo,
            "attendees": final_attendees,
            "source": "minimax",
        }

    except Exception:
        return _fallback_parse(query, organizer, attendees, repo_hint, topic_hint, settings)