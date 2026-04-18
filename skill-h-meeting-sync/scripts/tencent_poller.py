"""
tencent_poller.py
拉取腾讯会议未来 7 天的会议列表。

对外暴露：
  poll_tencent_meetings() -> list[dict]

每个元素结构：
{
  "meeting_id":     str,
  "meeting_code":   str,
  "topic":          str,
  "join_url":       str,
  "start_time":     int,   # Unix 时间戳（秒）
  "end_time":       int,
  "start_time_str": str,   # 北京时间可读字符串 YYYY-MM-DD HH:MM
  "status":         int    # 腾讯会议状态码 1=未开始 2=进行中 3=已结束
}
"""

import os
import time
import hmac
import hashlib
import datetime
import requests
import pytz

TENCENT_APP_ID      = os.environ.get("TENCENT_APP_ID", "")
TENCENT_SECRET_ID   = os.environ.get("TENCENT_SECRET_ID", "")
TENCENT_SECRET_KEY  = os.environ.get("TENCENT_SECRET_KEY", "")
TENCENT_SDK_ID      = os.environ.get("TENCENT_SDK_ID", "")
TENCENT_OPERATOR_ID = os.environ.get("TENCENT_OPERATOR_ID", "")
TENCENT_API_BASE    = "https://api.meeting.qq.com"

TZ = pytz.timezone("Asia/Shanghai")


def _build_headers(method: str, path: str, body: str = "") -> dict:
    timestamp = str(int(time.time()))
    nonce     = "12345"
    sign_str  = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body}"
    sign = hmac.new(
        TENCENT_SECRET_KEY.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type":    "application/json",
        "AppId":           TENCENT_APP_ID,
        "SdkId":           TENCENT_SDK_ID,
        "X-TC-Key":        TENCENT_SECRET_ID,
        "X-TC-Timestamp":  timestamp,
        "X-TC-Nonce":      nonce,
        "X-TC-Signature":  sign,
        "X-TC-Registered": "1"
    }


def _ts_to_str(ts: int) -> str:
    """Unix 时间戳转北京时间可读字符串。"""
    try:
        dt = datetime.datetime.fromtimestamp(ts, tz=TZ)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def poll_tencent_meetings() -> list[dict]:
    """
    拉取当前用户未来 7 天的会议列表。
    使用腾讯会议 GET /v1/meetings 接口（查询用户会议列表）。
    """
    now     = int(time.time())
    week    = 7 * 24 * 3600
    end_ts  = now + week

    path   = "/v1/meetings"
    params = {
        "userid":      TENCENT_OPERATOR_ID,
        "instanceid":  1,
        "start_time":  str(now),
        "end_time":    str(end_ts),
        "page":        1,
        "page_size":   100
    }

    # 构造查询字符串用于签名（GET 请求 body 为空）
    headers = _build_headers("GET", path, "")

    try:
        resp = requests.get(
            f"{TENCENT_API_BASE}{path}",
            headers=headers,
            params=params,
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[tencent_poller] 拉取会议列表失败: {e}")
        return []

    raw_list = data.get("meeting_info_list", [])
    result   = []

    for m in raw_list:
        start_ts = int(m.get("start_time", 0))
        end_ts_m = int(m.get("end_time",   0))
        result.append({
            "meeting_id":     m.get("meeting_id", ""),
            "meeting_code":   m.get("meeting_code", ""),
            "topic":          m.get("subject", ""),
            "join_url":       m.get("join_url", ""),
            "start_time":     start_ts,
            "end_time":       end_ts_m,
            "start_time_str": _ts_to_str(start_ts),
            "status":         m.get("status", 1)
        })

    print(f"[tencent_poller] 拉取到 {len(result)} 个腾讯会议")
    return result