# -*- coding: utf-8 -*-

import json
import shlex
import subprocess
from datetime import timedelta
from typing import Any, Dict, List, Optional

import requests

from common import SkillError, now_local


class TencentMeetingClient:
    """
    这是一个适配层，不是重写腾讯会议 skill。

    支持三种模式：
    1. mock：本地测试
    2. http_bridge：POST 到你已有的桥接服务
    3. command_bridge：subprocess 调你已有命令
    """

    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings
        self.mode = settings.get("TENCENT_MEETING_MODE", "mock")

    def create_meeting(
        self,
        topic: str,
        scheduled_time,
        duration_minutes: int,
        organizer: str,
        attendees: List[str],
        recurrence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if self.mode == "mock":
            return self._create_mock(topic, scheduled_time, duration_minutes)

        if self.mode == "http_bridge":
            return self._create_via_http_bridge(
                topic=topic,
                scheduled_time=scheduled_time,
                duration_minutes=duration_minutes,
                organizer=organizer,
                attendees=attendees,
                recurrence=recurrence,
            )

        if self.mode == "command_bridge":
            return self._create_via_command_bridge(
                topic=topic,
                scheduled_time=scheduled_time,
                duration_minutes=duration_minutes,
                organizer=organizer,
                attendees=attendees,
                recurrence=recurrence,
            )

        raise SkillError(f"不支持的腾讯会议模式：{self.mode}", stage="create_meeting")

    def cancel_meeting(self, meeting_id: str) -> bool:
        if self.mode == "mock":
            return True

        if self.mode == "http_bridge":
            url = self.settings.get("TENCENT_MEETING_CANCEL_URL")
            if not url:
                return False
            headers = {}
            bridge_token = self.settings.get("TENCENT_MEETING_BRIDGE_TOKEN")
            if bridge_token:
                headers["Authorization"] = f"Bearer {bridge_token}"

            resp = requests.post(url, json={"meeting_id": meeting_id}, headers=headers, timeout=30)
            if resp.status_code >= 300:
                return False
            return True

        if self.mode == "command_bridge":
            cmd = self.settings.get("TENCENT_MEETING_COMMAND")
            if not cmd:
                return False

            payload = {
                "action": "cancel_meeting",
                "meeting_id": meeting_id,
            }
            full_cmd = shlex.split(cmd) + [json.dumps(payload, ensure_ascii=False)]
            proc = subprocess.run(full_cmd, capture_output=True, text=True, timeout=60)
            return proc.returncode == 0

        return False

    def _create_mock(self, topic: str, scheduled_time, duration_minutes: int) -> Dict[str, Any]:
        meeting_id = f"mock-{int(scheduled_time.timestamp())}"
        meeting_code = str(int(scheduled_time.timestamp()))[-9:]
        join_url = f"https://meeting.tencent.com/mock/{meeting_id}"

        return {
            "meeting_id": meeting_id,
            "meeting_code": meeting_code,
            "join_url": join_url,
            "start_time": scheduled_time.isoformat(),
            "end_time": (scheduled_time + timedelta(minutes=duration_minutes)).isoformat(),
        }

    def _create_via_http_bridge(
        self,
        topic: str,
        scheduled_time,
        duration_minutes: int,
        organizer: str,
        attendees: List[str],
        recurrence: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        url = self.settings.get("TENCENT_MEETING_BRIDGE_URL")
        if not url:
            raise SkillError("缺少 TENCENT_MEETING_BRIDGE_URL", stage="create_meeting")

        payload = {
            "topic": topic,
            "scheduled_time": scheduled_time.isoformat(),
            "duration_minutes": duration_minutes,
            "organizer": organizer,
            "attendees": attendees,
            "recurrence": recurrence,
        }

        headers = {"Content-Type": "application/json"}
        bridge_token = self.settings.get("TENCENT_MEETING_BRIDGE_TOKEN")
        if bridge_token:
            headers["Authorization"] = f"Bearer {bridge_token}"

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise SkillError(f"腾讯会议桥接接口调用失败：{e}", stage="create_meeting")

        if not data.get("ok"):
            raise SkillError(f"腾讯会议创建失败：{data}", stage="create_meeting")

        result = data.get("data", data)

        required = ["meeting_id", "meeting_code", "join_url"]
        for key in required:
            if key not in result or not result[key]:
                raise SkillError(f"腾讯会议返回缺少字段：{key}", stage="create_meeting")

        return {
            "meeting_id": str(result["meeting_id"]),
            "meeting_code": str(result["meeting_code"]),
            "join_url": result["join_url"],
            "start_time": result.get("start_time", scheduled_time.isoformat()),
            "end_time": result.get("end_time", (scheduled_time + timedelta(minutes=duration_minutes)).isoformat()),
        }

    def _create_via_command_bridge(
        self,
        topic: str,
        scheduled_time,
        duration_minutes: int,
        organizer: str,
        attendees: List[str],
        recurrence: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        cmd = self.settings.get("TENCENT_MEETING_COMMAND")
        if not cmd:
            raise SkillError("缺少 TENCENT_MEETING_COMMAND", stage="create_meeting")

        payload = {
            "action": "create_meeting",
            "topic": topic,
            "scheduled_time": scheduled_time.isoformat(),
            "duration_minutes": duration_minutes,
            "organizer": organizer,
            "attendees": attendees,
            "recurrence": recurrence,
        }

        try:
            full_cmd = shlex.split(cmd) + [json.dumps(payload, ensure_ascii=False)]
            proc = subprocess.run(full_cmd, capture_output=True, text=True, timeout=90)
        except Exception as e:
            raise SkillError(f"命令桥接调用失败：{e}", stage="create_meeting")

        if proc.returncode != 0:
            raise SkillError(
                f"命令桥接返回非零退出码：{proc.returncode}，stderr={proc.stderr.strip()}",
                stage="create_meeting",
            )

        try:
            data = json.loads(proc.stdout.strip())
        except Exception as e:
            raise SkillError(f"命令桥接输出不是合法 JSON：{e}", stage="create_meeting")

        if not data.get("ok"):
            raise SkillError(f"腾讯会议创建失败：{data}", stage="create_meeting")

        result = data.get("data", data)
        return {
            "meeting_id": str(result["meeting_id"]),
            "meeting_code": str(result["meeting_code"]),
            "join_url": result["join_url"],
            "start_time": result.get("start_time", scheduled_time.isoformat()),
            "end_time": result.get("end_time", (scheduled_time + timedelta(minutes=duration_minutes)).isoformat()),
        }