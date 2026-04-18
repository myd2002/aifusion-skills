"""
pending_store.py
管理归属未确认的"待处理新增会议"（repo: pending 状态）。

当腾讯会议侧有新会议但无法自动判断项目归属时，
暂存到 aifusion-meta/pending/MEETING_ID.yaml，
并通知组织者确认。

30 分钟内无响应后，该记录保持 pending 状态，
等待组织者后续通过 OpenClaw 指定仓库（由 Skill-A 的 repo-reply 流程处理）。

对外暴露：
  save_pending(tencent_item)         # 写入 pending 记录
  load_all_pending() -> list[dict]   # 读取所有 pending 记录
  delete_pending(meeting_id)         # 删除 pending 记录（归属确认后调用）
"""

import os
import yaml
import datetime
import pytz

from scripts.common    import AIFUSION_META_REPO, now_beijing, write_log
from scripts.gitea_ops import create_file, read_file, update_file, gitea_get

TZ = pytz.timezone("Asia/Shanghai")


def _pending_path(meeting_id: str) -> str:
    return f"pending/{meeting_id}.yaml"


def save_pending(tencent_item: dict) -> bool:
    """
    把新增的腾讯会议写入 aifusion-meta/pending/MEETING_ID.yaml。
    已存在则跳过（幂等）。
    """
    meeting_id = tencent_item.get("meeting_id", "")
    if not meeting_id:
        return False

    path = _pending_path(meeting_id)

    # 幂等：已存在则跳过
    existing, _ = read_file(AIFUSION_META_REPO, path)
    if existing:
        print(f"[pending_store] {meeting_id} 已在 pending，跳过")
        return True

    record = {
        "meeting_id":     meeting_id,
        "meeting_code":   tencent_item.get("meeting_code", ""),
        "topic":          tencent_item.get("topic", ""),
        "join_url":       tencent_item.get("join_url", ""),
        "start_time_str": tencent_item.get("start_time_str", ""),
        "start_time":     tencent_item.get("start_time", 0),
        "end_time":       tencent_item.get("end_time", 0),
        "repo":           "pending",
        "created_at":     now_beijing().isoformat(),
        "notified_at":    now_beijing().isoformat()
    }

    content = yaml.dump(record, allow_unicode=True, default_flow_style=False)
    ok = create_file(
        AIFUSION_META_REPO,
        path,
        content,
        f"chore: add pending meeting {meeting_id}"
    )
    if ok:
        print(f"[pending_store] 已暂存 pending 会议: {meeting_id}")
    return ok


def load_all_pending() -> list[dict]:
    """
    读取 aifusion-meta/pending/ 下所有 .yaml 文件，返回 pending 记录列表。
    """
    from scripts.common import GITEA_BASE_URL
    owner, reponame = AIFUSION_META_REPO.split("/", 1)
    contents = gitea_get(
        f"/api/v1/repos/{owner}/{reponame}/contents/pending"
    )
    if not contents or not isinstance(contents, list):
        return []

    result = []
    for item in contents:
        if not item.get("name", "").endswith(".yaml"):
            continue
        content, sha = read_file(AIFUSION_META_REPO, f"pending/{item['name']}")
        if not content:
            continue
        try:
            record = yaml.safe_load(content)
            record["_sha"] = sha
            result.append(record)
        except Exception:
            continue

    return result


def delete_pending(meeting_id: str) -> bool:
    """
    归属确认后删除 pending 记录。
    通过把文件内容标记为 resolved 来实现（Gitea API 不直接支持删除文件内容为空）。
    实际删除通过 Gitea DELETE /contents API 实现。
    """
    import requests
    import base64
    from scripts.common import GITEA_BASE_URL, GITEA_TOKEN_BOT

    path     = _pending_path(meeting_id)
    owner, reponame = AIFUSION_META_REPO.split("/", 1)

    _, sha = read_file(AIFUSION_META_REPO, path)
    if not sha:
        return True  # 已不存在，视为成功

    url = f"{GITEA_BASE_URL}/api/v1/repos/{owner}/{reponame}/contents/{path}"
    headers = {
        "Authorization": f"token {GITEA_TOKEN_BOT}",
        "Content-Type":  "application/json"
    }
    payload = {
        "message": f"chore: resolve pending meeting {meeting_id}",
        "sha":     sha
    }
    try:
        resp = requests.delete(url, headers=headers, json=payload, timeout=10)
        return resp.status_code in (200, 204)
    except Exception as e:
        print(f"[pending_store] 删除 pending 失败 {meeting_id}: {e}")
        return False