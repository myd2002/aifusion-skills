"""
handlers.py
处理 diff_engine 产生的四类变更结果。

handle_added(tencent_item)
  腾讯有 + Gitea 无：无法自动判断归属，暂存 pending，通知组织者确认

handle_cancelled(gitea_item)
  Gitea 有 + 腾讯无：status → cancelled，SMTP 通知全员

handle_rescheduled(gitea_item, tencent_item)
  时间不同：旧目录 status → rescheduled + __rescheduled 后缀重命名
            新目录 status = scheduled，继承 organizer / agenda / attendees
            SMTP 通知全员

handle_archive(archivable_items)
  status ∈ {cancelled, rescheduled} 且超过 30 天：移入 meetings/archive/
"""

import os
import datetime
import yaml
import pytz
import base64
import requests

from scripts.common      import (
    AIFUSION_META_REPO, GITEA_BASE_URL, GITEA_TOKEN_BOT,
    now_beijing, format_meeting_dir, write_log,
    gitea_headers
)
from scripts.gitea_ops   import (
    read_file, create_file, update_file,
    update_meta_yaml, get_all_member_emails
)
from scripts.email_sender import send_plain_email
from scripts.pending_store import save_pending

TZ = pytz.timezone("Asia/Shanghai")


# ── 新增处理 ──────────────────────────────────────────────

def handle_added(tencent_item: dict):
    """
    腾讯会议侧新增、Gitea 无对应目录。
    无法自动判断项目归属 → 暂存 pending → 通知组织者确认归属。
    """
    meeting_id   = tencent_item.get("meeting_id", "")
    topic        = tencent_item.get("topic", "")
    start_str    = tencent_item.get("start_time_str", "")
    meeting_code = tencent_item.get("meeting_code", "")
    join_url     = tencent_item.get("join_url", "")

    print(f"[handlers] 新增会议: {meeting_id} {topic} {start_str}")

    # 暂存 pending
    save_pending(tencent_item)

    # 通知组织者（发给 ADVISOR）
    _notify_organizer_new_meeting(
        meeting_id=meeting_id,
        topic=topic,
        start_str=start_str,
        meeting_code=meeting_code,
        join_url=join_url
    )

    write_log(
        "skill-h", AIFUSION_META_REPO, meeting_id,
        "new-meeting-pending", "ok",
        {"topic": topic, "start_time": start_str}
    )


def _notify_organizer_new_meeting(meeting_id: str, topic: str,
                                  start_str: str, meeting_code: str,
                                  join_url: str):
    """通知组织者确认新会议的项目归属。"""
    from scripts.gitea_ops import get_user_email
    from scripts.common    import ADVISOR_USERNAME

    advisor_email = get_user_email(ADVISOR_USERNAME) if ADVISOR_USERNAME else None
    if not advisor_email:
        print(f"[handlers] 找不到 advisor 邮箱，跳过新增通知")
        return

    subject = f"【需要确认】腾讯会议发现新会议：{topic}"
    body = f"""您好，

AIFusionBot 在腾讯会议中检测到一个未在 Gitea 登记的会议，请确认其项目归属：

📅 主题：{topic}
🕐 时间：{start_str}
📋 会议号：{meeting_code}
🔗 入会链接：{join_url}
🆔 会议 ID：{meeting_id}

请在 OpenClaw 中回复：
  "meeting_id={meeting_id} 归属到 [项目名称/跨项目]"

若无需追踪此会议，可忽略此邮件。AIFusionBot 将继续等待您的确认。

---
此邮件由 AIFusionBot 自动发送，请勿直接回复。
"""
    send_plain_email([advisor_email], subject, body)
    print(f"[handlers] 已通知 advisor 确认归属: {meeting_id}")


# ── 取消处理 ──────────────────────────────────────────────

def handle_cancelled(gitea_item: dict):
    """
    Gitea 有、腾讯侧已消失 → 置 cancelled，通知全员。
    """
    repo        = gitea_item["repo"]
    meeting_dir = gitea_item["meeting_dir"]
    meta        = gitea_item["meta"]
    sha         = gitea_item["sha"]

    topic          = meta.get("topic", "团队会议")
    scheduled_time = meta.get("scheduled_time", "")

    print(f"[handlers] 取消会议: {repo}/{meeting_dir}")

    # 更新 meta.yaml
    ok = update_meta_yaml(
        repo, meeting_dir,
        {"status": "cancelled", "cancelled_at": now_beijing().isoformat()},
        sha
    )
    if not ok:
        print(f"[handlers] meta.yaml 更新失败 {repo}/{meeting_dir}")
        return

    # 通知全员
    _notify_all_cancelled(repo, meeting_dir, meta, topic, scheduled_time)

    write_log(
        "skill-h", repo, meeting_dir,
        "meeting-cancelled", "ok",
        {"topic": topic, "scheduled_time": scheduled_time}
    )


def _notify_all_cancelled(repo: str, meeting_dir: str, meta: dict,
                          topic: str, scheduled_time: str):
    email_map = get_all_member_emails(repo)
    emails    = list(email_map.values())
    if not emails:
        return

    subject = f"【会议取消】{topic} - {scheduled_time}"
    body = f"""您好，

以下会议已取消（在腾讯会议中被删除）：

📅 主题：{topic}
🕐 原定时间：{scheduled_time}

如有疑问，请联系会议组织者。

---
此邮件由 AIFusionBot 自动发送，请勿直接回复。
"""
    send_plain_email(emails, subject, body)
    print(f"[handlers] 取消通知已发送，收件人 {len(emails)} 人")


# ── 改期处理 ──────────────────────────────────────────────

def handle_rescheduled(gitea_item: dict, tencent_item: dict):
    """
    同一 meeting_id，腾讯侧时间与 Gitea 不同 → 改期处理：
    1. 旧目录 meta.yaml: status → rescheduled，目录名追加 __rescheduled
    2. 新目录（新时间）：创建 meta.yaml + agenda.md，继承 organizer/attendees/agenda
    3. 通知全员
    """
    repo        = gitea_item["repo"]
    meeting_dir = gitea_item["meeting_dir"]
    meta        = gitea_item["meta"]
    sha         = gitea_item["sha"]

    topic        = meta.get("topic", "团队会议")
    old_time_str = meta.get("scheduled_time", "")
    new_time_str = tencent_item.get("start_time_str", "")
    meeting_id   = tencent_item.get("meeting_id", "")
    join_url     = tencent_item.get("join_url", meta.get("join_url", ""))
    meeting_code = tencent_item.get("meeting_code", meta.get("meeting_code", ""))

    print(f"[handlers] 改期会议: {repo}/{meeting_dir} {old_time_str} → {new_time_str}")

    # ── Step 1: 旧目录置 rescheduled ──────────────────────
    update_meta_yaml(
        repo, meeting_dir,
        {
            "status":        "rescheduled",
            "rescheduled_at": now_beijing().isoformat(),
            "rescheduled_to": new_time_str
        },
        sha
    )

    # ── Step 2: 新目录 ────────────────────────────────────
    new_dir = _make_new_dir(tencent_item)
    if not new_dir:
        print(f"[handlers] 无法生成新目录名，跳过改期处理")
        return

    new_meta = _build_rescheduled_meta(
        old_meta=meta,
        new_dir=new_dir,
        new_time_str=new_time_str,
        tencent_item=tencent_item,
        old_dir=meeting_dir
    )
    new_agenda = _inherit_agenda(repo, meeting_dir, new_time_str, join_url,
                                 meeting_code, topic, meta.get("attendees", []))

    create_file(
        repo,
        f"meetings/{new_dir}/meta.yaml",
        yaml.dump(new_meta, allow_unicode=True, default_flow_style=False),
        f"feat: reschedule meeting {meeting_dir} → {new_dir}"
    )
    create_file(
        repo,
        f"meetings/{new_dir}/agenda.md",
        new_agenda,
        f"feat: inherit agenda for rescheduled meeting {new_dir}"
    )

    # ── Step 3: 通知全员 ──────────────────────────────────
    _notify_all_rescheduled(repo, meta, topic, old_time_str,
                            new_time_str, join_url, meeting_code)

    write_log(
        "skill-h", repo, meeting_dir,
        "meeting-rescheduled", "ok",
        {
            "old_time": old_time_str,
            "new_time": new_time_str,
            "new_dir":  new_dir
        }
    )


def _make_new_dir(tencent_item: dict) -> str | None:
    """根据腾讯会议的新时间生成新目录名。"""
    start_ts = tencent_item.get("start_time", 0)
    if not start_ts:
        return None
    try:
        dt = datetime.datetime.fromtimestamp(start_ts, tz=TZ)
        return format_meeting_dir(dt)
    except Exception:
        return None


def _build_rescheduled_meta(old_meta: dict, new_dir: str,
                             new_time_str: str, tencent_item: dict,
                             old_dir: str) -> dict:
    """基于旧 meta 构造新目录的 meta.yaml 内容。"""
    end_ts = tencent_item.get("end_time", 0)
    end_str = ""
    if end_ts:
        try:
            dt = datetime.datetime.fromtimestamp(end_ts, tz=TZ)
            end_str = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass

    return {
        "meeting_dir":      new_dir,
        "repo":             old_meta.get("repo", ""),
        "topic":            old_meta.get("topic", ""),
        "meeting_id":       tencent_item.get("meeting_id", old_meta.get("meeting_id", "")),
        "meeting_code":     tencent_item.get("meeting_code", old_meta.get("meeting_code", "")),
        "join_url":         tencent_item.get("join_url", old_meta.get("join_url", "")),
        "scheduled_time":   new_time_str,
        "end_time":         end_str,
        "organizer":        old_meta.get("organizer", ""),
        "attendees":        old_meta.get("attendees", []),
        "meeting_category": old_meta.get("meeting_category", "single-project"),
        "series_id":        old_meta.get("series_id", ""),
        "recurrence":       old_meta.get("recurrence"),
        "status":           "scheduled",
        "rescheduled_from": old_dir,
        "created_at":       now_beijing().isoformat()
    }


def _inherit_agenda(repo: str, old_dir: str, new_time_str: str,
                    join_url: str, meeting_code: str,
                    topic: str, attendees: list[str]) -> str:
    """
    继承旧 agenda.md 的议题部分，更新时间和链接。
    找不到旧 agenda 则生成最简版本。
    """
    old_agenda, _ = read_file(repo, f"meetings/{old_dir}/agenda.md")
    attendees_str = "\n".join(f"- {a}" for a in attendees) if attendees else "- （全体成员）"

    # 提取旧 agenda 的议题部分（## 本次议题 之后的内容）
    topics_section = ""
    if old_agenda:
        marker = "## 本次议题"
        idx = old_agenda.find(marker)
        if idx != -1:
            # 取到下一个 ## 之前
            rest = old_agenda[idx + len(marker):]
            next_h2 = rest.find("\n## ")
            topics_section = rest[:next_h2].strip() if next_h2 != -1 else rest.strip()

    if not topics_section:
        topics_section = "1. \n2. \n3. "

    return f"""# 会议 Agenda（改期版）

## 基本信息

- **主题**：{topic}
- **时间**：{new_time_str}（已从原时间改期）
- **会议号**：{meeting_code}
- **入会链接**：{join_url}

## 参会人员

{attendees_str}

## 本次议题

{topics_section}

## 备注

> 本 agenda 由 AIFusionBot 从改期前的会议继承生成。

"""


def _notify_all_rescheduled(repo: str, meta: dict, topic: str,
                             old_time: str, new_time: str,
                             join_url: str, meeting_code: str):
    email_map = get_all_member_emails(repo)
    emails    = list(email_map.values())
    if not emails:
        return

    subject = f"【会议改期】{topic} 时间已变更"
    body = f"""您好，

以下会议时间已变更，请注意更新日程：

📅 主题：{topic}
❌ 原定时间：{old_time}
✅ 新时间：{new_time}
📋 会议号：{meeting_code}
🔗 入会链接：{join_url}

---
此邮件由 AIFusionBot 自动发送，请勿直接回复。
"""
    send_plain_email(emails, subject, body)
    print(f"[handlers] 改期通知已发送，收件人 {len(emails)} 人")


# ── 归档清理 ──────────────────────────────────────────────

def handle_archive(archivable_items: list[dict]):
    """
    把 status ∈ {cancelled, rescheduled} 且超过 30 天的会议目录
    移入 meetings/archive/。

    实现方式：
    1. 读取目录下所有文件
    2. 在 meetings/archive/MEETING_DIR/ 下重建
    3. 更新 meta.yaml status → archived
    （Gitea API 不支持直接移动目录，通过逐文件复制+标记实现）
    """
    if not archivable_items:
        return

    print(f"[handlers] 归档 {len(archivable_items)} 个历史会议")

    for item in archivable_items:
        repo        = item["repo"]
        meeting_dir = item["meeting_dir"]
        meta        = item["meta"]
        sha         = item["sha"]

        try:
            _archive_one(repo, meeting_dir, meta, sha)
        except Exception as e:
            print(f"[handlers] 归档失败 {repo}/{meeting_dir}: {e}")
            write_log(
                "skill-h", repo, meeting_dir,
                "archive-failed", "error", {"error": str(e)}
            )


def _archive_one(repo: str, meeting_dir: str, meta: dict, sha: str):
    """归档单个会议目录。"""
    owner, reponame = repo.split("/", 1)

    # 列出会议目录下的所有文件
    from scripts.common import gitea_get
    contents = gitea_get(
        f"/api/v1/repos/{owner}/{reponame}/contents/meetings/{meeting_dir}"
    )
    if not contents or not isinstance(contents, list):
        print(f"[handlers] 归档：无法列出目录内容 {meeting_dir}")
        return

    # 逐文件复制到 archive 目录
    files_copied = 0
    for item in contents:
        if item.get("type") != "file":
            continue
        filename = item["name"]
        old_path = f"meetings/{meeting_dir}/{filename}"
        new_path = f"meetings/archive/{meeting_dir}/{filename}"

        content, _ = read_file(repo, old_path)
        if content is None:
            continue

        ok = create_file(
            repo, new_path, content,
            f"chore: archive {meeting_dir}/{filename}"
        )
        if ok:
            files_copied += 1

    # 更新原目录 meta.yaml 为 archived（不删除原文件，保留历史）
    update_meta_yaml(
        repo, meeting_dir,
        {
            "status":      "archived",
            "archived_at": now_beijing().isoformat()
        },
        sha
    )

    write_log(
        "skill-h", repo, meeting_dir,
        "meeting-archived", "ok",
        {"files_copied": files_copied}
    )
    print(f"[handlers] ✅ 归档完成 {repo}/{meeting_dir}，复制 {files_copied} 个文件")