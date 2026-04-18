"""
main.py
Skill-C fetch_minutes cron 入口。
每 10 分钟由 cron 调用一次。

处理逻辑：
  A 类（brief-sent 已结束）
    → status 推进到 waiting-transcript，等下轮处理

  B 类（waiting-transcript）
    → 未超时：三层降级拉取转录内容
        成功：写文件 → 两阶段抽取 → 写 draft_issue.md
              → status: draft-pending-review → 邮件通知组织者
        失败：保持 waiting-transcript，等下轮重试
    → 已超时：status: transcript-failed → 邮件通知组织者手动上传

  C 类（transcript-failed 且已手动上传 transcript.md）
    → 读取手动上传的 transcript.md → 两阶段抽取 → 写 draft_issue.md
      → status: draft-pending-review → 邮件通知组织者
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from scripts.meeting_classifier import classify_meetings, is_timeout
from scripts.transcript_fetcher  import fetch_meeting_content, check_meeting_ended
from scripts.issue_extractor     import extract_issues
from scripts.draft_writer        import write_meeting_documents
from scripts.gitea_ops           import (
    update_meta_yaml, read_file, get_all_member_emails
)
from scripts.email_sender        import send_plain_email
from scripts.common              import (
    write_log, now_beijing, GITEA_BASE_URL, AIFUSION_META_REPO
)


# ── A 类处理 ──────────────────────────────────────────────

def handle_class_a(item: dict):
    """
    brief-sent 且会议已结束 → 推进到 waiting-transcript。
    同时通过腾讯会议 API 二次确认会议是否真的结束。
    """
    repo        = item["repo"]
    meeting_dir = item["meeting_dir"]
    meta        = item["meta"]
    sha         = item["sha"]

    meeting_id = meta.get("meeting_id", "")

    # 腾讯会议 API 确认（失败则依赖 end_time 判断，meeting_classifier 已处理）
    if meeting_id:
        ended = check_meeting_ended(meeting_id)
        if not ended:
            print(f"[Skill-C] A类 {meeting_dir}: 腾讯会议API显示未结束，跳过")
            return

    print(f"[Skill-C] A类 {meeting_dir}: 推进到 waiting-transcript")
    ok = update_meta_yaml(
        repo, meeting_dir,
        {
            "status":            "waiting-transcript",
            "waiting_since":     now_beijing().isoformat()
        },
        sha
    )
    if ok:
        write_log("skill-c", repo, meeting_dir,
                  "status-waiting-transcript", "ok")
    else:
        write_log("skill-c", repo, meeting_dir,
                  "status-waiting-transcript", "error",
                  {"error": "update_meta_yaml failed"})


# ── B 类处理 ──────────────────────────────────────────────

def handle_class_b(item: dict):
    """
    waiting-transcript → 拉取内容或超时处理。
    """
    repo        = item["repo"]
    meeting_dir = item["meeting_dir"]
    meta        = item["meta"]
    sha         = item["sha"]

    now = now_beijing()

    # 超时判断
    if is_timeout(meta, now):
        print(f"[Skill-C] B类 {meeting_dir}: 超时，置 transcript-failed")
        _handle_timeout(repo, meeting_dir, meta, sha)
        return

    # 尝试拉取转录内容
    meeting_id = meta.get("meeting_id", "")
    if not meeting_id:
        print(f"[Skill-C] B类 {meeting_dir}: 无 meeting_id，跳过")
        return

    content = fetch_meeting_content(meeting_id)
    if not content:
        print(f"[Skill-C] B类 {meeting_dir}: 内容暂不可用，等待下轮")
        write_log("skill-c", repo, meeting_dir, "poll-transcript", "pending")
        return

    # 拉取成功，进入抽取流程
    print(f"[Skill-C] B类 {meeting_dir}: 内容拉取成功，source={content['source']}")
    _process_content(repo, meeting_dir, meta, sha, content)


# ── C 类处理 ──────────────────────────────────────────────

def handle_class_c(item: dict):
    """
    transcript-failed 且组织者已手动上传 transcript.md。
    直接读取本地文件进入抽取流程。
    """
    repo        = item["repo"]
    meeting_dir = item["meeting_dir"]
    meta        = item["meta"]
    sha         = item["sha"]

    print(f"[Skill-C] C类 {meeting_dir}: 读取手动上传的 transcript.md")
    transcript_content, _ = read_file(
        repo, f"meetings/{meeting_dir}/transcript.md"
    )
    if not transcript_content:
        print(f"[Skill-C] C类 {meeting_dir}: 读取 transcript.md 失败，跳过")
        return

    content = {
        "source":     "transcript_only",
        "ai_summary": None,
        "transcript": transcript_content
    }
    _process_content(repo, meeting_dir, meta, sha, content)


# ── 公共：内容处理流程 ────────────────────────────────────

def _process_content(repo: str, meeting_dir: str, meta: dict,
                     sha: str, content: dict):
    """
    拿到会议内容后的统一处理流程：
    两阶段抽取 → 写文件 → status: draft-pending-review → 通知组织者
    """
    attendees = meta.get("attendees", [])

    # 两阶段 issue 抽取
    print(f"[Skill-C] {meeting_dir}: 开始 issue 抽取...")
    extracted = extract_issues(content, attendees)
    item_count = len(extracted.get("action_items", []))
    print(f"[Skill-C] {meeting_dir}: 抽取完成，共 {item_count} 条 action_item")

    # 写文件到 Gitea
    ok = write_meeting_documents(repo, meeting_dir, meta, content, extracted)
    if not ok:
        print(f"[Skill-C] {meeting_dir}: draft_issue.md 写入失败")
        write_log("skill-c", repo, meeting_dir,
                  "draft-write-failed", "error")
        return

    # 更新 meta.yaml
    _, current_sha = read_file(repo, f"meetings/{meeting_dir}/meta.yaml")
    update_meta_yaml(
        repo, meeting_dir,
        {"status": "draft-pending-review"},
        current_sha
    )

    # 通知组织者（不是全员）
    _notify_organizer(repo, meeting_dir, meta, item_count)

    write_log("skill-c", repo, meeting_dir,
              "draft-pending-review", "ok",
              {
                  "source":     content["source"],
                  "item_count": item_count
              })


def _notify_organizer(repo: str, meeting_dir: str,
                      meta: dict, item_count: int):
    """发送 draft_issue.md 审核通知给组织者。"""
    organizer = meta.get("organizer", "")
    topic     = meta.get("topic", "团队会议")

    # 查组织者邮箱
    from scripts.gitea_ops import get_user_email
    organizer_email = get_user_email(organizer) if organizer else None

    # fallback：发给 advisor
    if not organizer_email:
        from scripts.common import ADVISOR_USERNAME
        from scripts.gitea_ops import get_user_email as gue
        organizer_email = gue(ADVISOR_USERNAME) if ADVISOR_USERNAME else None

    if not organizer_email:
        print(f"[Skill-C] {meeting_dir}: 找不到组织者邮箱，跳过通知")
        return

    gitea_base   = GITEA_BASE_URL.rstrip("/")
    draft_url    = (f"{gitea_base}/{repo}/src/branch/main/"
                    f"meetings/{meeting_dir}/draft_issue.md")
    confirm_hint = (f"meetings/{meeting_dir}/draft_issue.md"
                    f" → meetings/{meeting_dir}/confirmed_issue.md")

    subject = f"【待审核】{topic} 的 issue 草稿已生成"
    body = f"""您好，

{topic}（{meeting_dir}）的 issue 草稿已由 AIFusionBot 自动生成，
共提取到 {item_count} 条行动项，请审核后确认。

📋 草稿链接：{draft_url}

【确认方式】

方式 A（Gitea 网页）：
  将文件重命名：{confirm_hint}

方式 B（OpenClaw 对话）：
  说"确认 {meeting_dir} 的 issue"

⚠️ 请仔细核对负责人、截止日期与依赖关系。
   如有问题请直接编辑草稿文件后再重命名确认。

---
此邮件由 AIFusionBot 自动发送，请勿直接回复。
"""
    send_plain_email([organizer_email], subject, body)
    print(f"[Skill-C] {meeting_dir}: 已通知组织者 {organizer_email}")


def _handle_timeout(repo: str, meeting_dir: str,
                    meta: dict, sha: str):
    """超时处理：置 transcript-failed，邮件通知组织者手动上传。"""
    from scripts.gitea_ops import get_user_email
    from scripts.common    import ADVISOR_USERNAME

    update_meta_yaml(
        repo, meeting_dir,
        {"status": "transcript-failed"},
        sha
    )

    organizer = meta.get("organizer", "")
    topic     = meta.get("topic", "团队会议")
    organizer_email = get_user_email(organizer) if organizer else None
    if not organizer_email and ADVISOR_USERNAME:
        organizer_email = get_user_email(ADVISOR_USERNAME)

    if organizer_email:
        gitea_base  = GITEA_BASE_URL.rstrip("/")
        upload_path = f"meetings/{meeting_dir}/transcript.md"
        upload_url  = (f"{gitea_base}/{repo}/src/branch/main/"
                       f"meetings/{meeting_dir}/")

        subject = f"【需要手动处理】{topic} 转录获取超时"
        body = f"""您好，

{topic}（{meeting_dir}）的会议转录内容超时未能自动获取。

请手动将会议转录文本上传到以下路径：
  仓库：{repo}
  路径：{upload_path}
  上传入口：{upload_url}

上传后，AIFusionBot 将在下一个 10 分钟周期自动读取并继续处理。

如腾讯会议提供了转录文件，可直接复制其内容并新建上述文件。

---
此邮件由 AIFusionBot 自动发送，请勿直接回复。
"""
        send_plain_email([organizer_email], subject, body)

    write_log("skill-c", repo, meeting_dir,
              "transcript-timeout", "error",
              {"status": "transcript-failed"})


# ── 主入口 ────────────────────────────────────────────────

def main():
    print(f"[Skill-C] 开始扫描 {now_beijing().isoformat()}")

    class_a, class_b, class_c = classify_meetings()
    print(f"[Skill-C] A类={len(class_a)} B类={len(class_b)} C类={len(class_c)}")

    for item in class_a:
        try:
            handle_class_a(item)
        except Exception as e:
            _log_error(item, "handle-class-a", e)

    for item in class_b:
        try:
            handle_class_b(item)
        except Exception as e:
            _log_error(item, "handle-class-b", e)

    for item in class_c:
        try:
            handle_class_c(item)
        except Exception as e:
            _log_error(item, "handle-class-c", e)

    print(f"[Skill-C] 本轮扫描完成")


def _log_error(item: dict, action: str, e: Exception):
    repo        = item.get("repo", "unknown")
    meeting_dir = item.get("meeting_dir", "unknown")
    print(f"[Skill-C] 处理失败 {repo}/{meeting_dir} action={action}: {e}")
    write_log("skill-c", repo, meeting_dir, action, "error",
              {"error": str(e)})


if __name__ == "__main__":
    main()