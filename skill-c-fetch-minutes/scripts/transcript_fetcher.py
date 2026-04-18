"""
transcript_fetcher.py
三层降级策略拉取腾讯会议内容：

Layer 1：AI 智能纪要 + 转录原文（source: ai_summary）
Layer 2：仅转录原文（source: transcript_only）
Layer 3：两层均失败，返回 None，由调用方保持 waiting-transcript 等待重试

对外暴露：
  fetch_meeting_content(meeting_id) -> dict | None

返回结构：
{
  "source":      "ai_summary" | "transcript_only",
  "ai_summary":  str | None,   # Layer 1 成功时有值
  "transcript":  str           # 原始转录文本
}
"""

import os
import time
import hmac
import hashlib
import json
import requests

TENCENT_APP_ID      = os.environ.get("TENCENT_APP_ID", "")
TENCENT_SECRET_ID   = os.environ.get("TENCENT_SECRET_ID", "")
TENCENT_SECRET_KEY  = os.environ.get("TENCENT_SECRET_KEY", "")
TENCENT_SDK_ID      = os.environ.get("TENCENT_SDK_ID", "")
TENCENT_OPERATOR_ID = os.environ.get("TENCENT_OPERATOR_ID", "")
TENCENT_API_BASE    = "https://api.meeting.qq.com"


# ── 签名（与 tencent_meeting.py 保持一致）────────────────

def _build_headers(method: str, path: str, body: str) -> dict:
    timestamp = str(int(time.time()))
    nonce     = "12345"
    sign_str  = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body}"
    sign = hmac.new(
        TENCENT_SECRET_KEY.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type":   "application/json",
        "AppId":          TENCENT_APP_ID,
        "SdkId":          TENCENT_SDK_ID,
        "X-TC-Key":       TENCENT_SECRET_ID,
        "X-TC-Timestamp": timestamp,
        "X-TC-Nonce":     nonce,
        "X-TC-Signature": sign,
        "X-TC-Registered": "1"
    }


def _get(path: str, params: dict = None) -> dict | None:
    """发送 GET 请求到腾讯会议 API。"""
    body = ""
    headers = _build_headers("GET", path, body)
    url = f"{TENCENT_API_BASE}{path}"
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[transcript_fetcher] GET {path} 失败: {e}")
        return None


# ── 获取录制文件列表 ──────────────────────────────────────

def _get_recordings(meeting_id: str) -> list[dict]:
    """
    拉取会议的云录制列表。
    返回 record_file_list，每个元素含 record_file_id、download_url 等。
    """
    path = f"/v1/addresses/{meeting_id}/recording-files"
    params = {"userid": TENCENT_OPERATOR_ID, "instanceid": 1}
    data = _get(path, params)
    if not data:
        return []
    return data.get("record_file_list", [])


# ── Layer 1：AI 智能纪要 ──────────────────────────────────

def _fetch_ai_summary(meeting_id: str) -> str | None:
    """
    拉取腾讯会议 AI 智能纪要。
    接口：GET /v1/meetings/{meeting_id}/ai-summary
    成功返回纪要文本，失败返回 None。
    """
    path = f"/v1/meetings/{meeting_id}/ai-summary"
    params = {"userid": TENCENT_OPERATOR_ID, "instanceid": 1}
    data = _get(path, params)
    if not data:
        return None

    # 腾讯会议 AI 纪要可能在不同字段，尝试多个
    summary = (
        data.get("summary")
        or data.get("ai_summary")
        or data.get("content")
        or ""
    )
    return summary.strip() if summary.strip() else None


# ── Layer 1 & 2：转录原文 ─────────────────────────────────

def _fetch_transcript(meeting_id: str) -> str | None:
    """
    拉取会议转录原文。
    先获取录制列表，再下载转录文件（.txt 或 .vtt 格式）。
    成功返回转录文本，失败返回 None。
    """
    recordings = _get_recordings(meeting_id)
    if not recordings:
        return None

    for rec in recordings:
        # 优先找转录文件
        transcript_url = rec.get("transcript_download_url") or rec.get("download_url", "")
        if not transcript_url:
            continue
        try:
            resp = requests.get(transcript_url, timeout=30)
            if resp.status_code == 200:
                raw = resp.text.strip()
                # 如果是 VTT 格式，去掉时间轴行，只保留文字
                if raw.startswith("WEBVTT"):
                    lines = []
                    for line in raw.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        if line == "WEBVTT":
                            continue
                        if "-->" in line:
                            continue
                        # 跳过纯数字行（序号）
                        if line.isdigit():
                            continue
                        lines.append(line)
                    return "\n".join(lines)
                return raw
        except Exception as e:
            print(f"[transcript_fetcher] 下载转录文件失败: {e}")
            continue

    return None


# ── 对外主入口 ────────────────────────────────────────────

def fetch_meeting_content(meeting_id: str) -> dict | None:
    """
    三层降级策略拉取会议内容。

    Layer 1：AI 智能纪要 + 转录原文
    Layer 2：仅转录原文
    Layer 3：均失败，返回 None

    返回：
    {
      "source":     "ai_summary" | "transcript_only",
      "ai_summary": str | None,
      "transcript": str
    }
    """
    print(f"[transcript_fetcher] 拉取会议内容 meeting_id={meeting_id}")

    # Layer 1：尝试拉 AI 智能纪要
    ai_summary = None
    try:
        ai_summary = _fetch_ai_summary(meeting_id)
    except Exception as e:
        print(f"[transcript_fetcher] Layer 1 AI 纪要失败: {e}")

    # 同时拉转录原文（AI 纪要成功也要拉，用于原话回填）
    transcript = None
    try:
        transcript = _fetch_transcript(meeting_id)
    except Exception as e:
        print(f"[transcript_fetcher] 转录原文拉取失败: {e}")

    if ai_summary and transcript:
        print(f"[transcript_fetcher] Layer 1 成功（ai_summary + transcript）")
        return {
            "source":     "ai_summary",
            "ai_summary": ai_summary,
            "transcript": transcript
        }

    if ai_summary and not transcript:
        # 有 AI 纪要但无转录，仍标记 ai_summary，transcript 用纪要内容代替
        print(f"[transcript_fetcher] Layer 1 部分成功（仅 ai_summary）")
        return {
            "source":     "ai_summary",
            "ai_summary": ai_summary,
            "transcript": ai_summary
        }

    if transcript and not ai_summary:
        print(f"[transcript_fetcher] Layer 2 成功（仅 transcript）")
        return {
            "source":     "transcript_only",
            "ai_summary": None,
            "transcript": transcript
        }

    # Layer 3：均失败
    print(f"[transcript_fetcher] Layer 3：内容暂不可用，等待重试")
    return None


def check_meeting_ended(meeting_id: str) -> bool:
    """
    查询腾讯会议状态，判断会议是否已结束。
    接口：GET /v1/meetings/{meeting_id}
    会议状态码：1=未开始, 2=进行中, 3=已结束, 4=已取消, 6=已过期
    """
    path = f"/v1/meetings/{meeting_id}"
    params = {
        "userid":     TENCENT_OPERATOR_ID,
        "instanceid": 1
    }
    data = _get(path, params)
    if not data:
        # API 失败时，保守地用结束时间判断（由调用方处理）
        return False

    meeting_info_list = data.get("meeting_info_list", [{}])
    if not meeting_info_list:
        return False

    state = meeting_info_list[0].get("current_sub_meeting_id")
    status_code = meeting_info_list[0].get("status", 0)
    # 3=已结束, 4=已取消, 6=已过期
    return status_code in (3, 4, 6)