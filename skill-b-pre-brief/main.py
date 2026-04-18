"""
main.py
Skill-B pre_brief cron 入口。
每 15 分钟由 cron 调用一次，完整流程：

1. 调用 repo_scanner 扫描所有仓库，找出需要发简报的会议
2. 对每次会议：
   a. 用 window_calculator 确定活动扫描时间窗口
   b. 用 activity_fetcher 拉取所有参会成员的 Gitea 活动
   c. 用 brief_generator 调用 MiniMax 生成摘要，合成 pre_brief.md
   d. 把 pre_brief.md commit 到对应仓库的会议目录
   e. SMTP 发送简报邮件给全员
   f. 更新 meta.yaml status → brief-sent
   g. 写日志
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from scripts.repo_scanner    import scan_pending_briefs
from scripts.window_calculator import get_scan_window
from scripts.activity_fetcher import fetch_all_members_activities
from scripts.brief_generator  import generate_brief
from scripts.gitea_ops        import create_file, update_meta_yaml, read_file, get_all_member_emails
from scripts.email_sender     import send_plain_email
from scripts.common           import write_log, now_beijing, GITEA_BASE_URL


def process_meeting(item: dict):
    """处理单次会议的简报生成与发送。"""
    repo        = item["repo"]
    meeting_dir = item["meeting_dir"]
    meta        = item["meta"]

    topic          = meta.get("topic", "团队会议")
    scheduled_time = meta.get("scheduled_time", "")
    join_url       = meta.get("join_url", "")
    meeting_code   = meta.get("meeting_code", "")
    attendees      = meta.get("attendees", [])

    print(f"[Skill-B] 处理会议: {repo}/{meeting_dir} topic={topic}")

    # ── Step 1: 计算活动扫描时间窗口 ──────────────────────
    since, until = get_scan_window(repo, meeting_dir, meta)
    print(f"[Skill-B] 扫描窗口: {since} ~ {until}")

    # ── Step 2: 拉取所有参会成员活动 ──────────────────────
    if not attendees:
        # fallback：从仓库成员列表获取
        email_map = get_all_member_emails(repo)
        attendees = list(email_map.keys())

    activities = fetch_all_members_activities(repo, attendees, since, until)

    # ── Step 3: 生成 pre_brief.md ─────────────────────────
    brief_content = generate_brief(
        topic=topic,
        meeting_dir=meeting_dir,
        scheduled_time=scheduled_time,
        join_url=join_url,
        meeting_code=meeting_code,
        since=since,
        until=until,
        activities=activities
    )

    # ── Step 4: commit pre_brief.md 到 Gitea ──────────────
    brief_path = f"meetings/{meeting_dir}/pre_brief.md"
    # 检查文件是否已存在（幂等：不重复创建）
    existing, _ = read_file(repo, brief_path)
    if existing:
        print(f"[Skill-B] pre_brief.md 已存在，跳过创建（幂等保护）")
    else:
        ok = create_file(repo, brief_path, brief_content,
                         f"feat: add pre_brief for {meeting_dir}")
        if not ok:
            print(f"[Skill-B] pre_brief.md 创建失败，跳过本次会议")
            write_log("skill-b", repo, meeting_dir, "brief-create-failed", "error")
            return

    # ── Step 5: 发送简报邮件给全员 ────────────────────────
    email_map = get_all_member_emails(repo)
    recipient_emails = list(email_map.values())

    brief_url = (f"{GITEA_BASE_URL}/{repo}/src/branch/main/"
                 f"meetings/{meeting_dir}/pre_brief.md")

    subject = f"【会前简报】{topic} - {scheduled_time}"
    body = f"""您好，

{topic} 的会前简报已生成，请在会议开始前查阅：

📋 简报链接：{brief_url}

🕐 会议时间：{scheduled_time}
📋 会议号：{meeting_code}
🔗 入会链接：{join_url}

---
以下是各成员工作进展摘要：

{_extract_summary_text(brief_content)}

---
此邮件由 AIFusionBot 自动发送，请勿直接回复。
"""

    email_ok = False
    if recipient_emails:
        email_ok = send_plain_email(recipient_emails, subject, body)
        if not email_ok:
            print(f"[Skill-B] 邮件发送失败（非致命），继续流程")
    else:
        print(f"[Skill-B] 未找到收件人邮箱，跳过邮件发送")

    # ── Step 6: 更新 meta.yaml status → brief-sent ────────
    _, sha = read_file(repo, f"meetings/{meeting_dir}/meta.yaml")
    update_meta_yaml(repo, meeting_dir, {"status": "brief-sent"}, sha)

    # ── Step 7: 写日志 ────────────────────────────────────
    write_log(
        skill="skill-b",
        repo=repo,
        meeting_dir=meeting_dir,
        action="brief-sent",
        status="ok",
        details={
            "recipients": len(recipient_emails),
            "email_ok":   email_ok,
            "since":      since.isoformat(),
            "until":      until.isoformat()
        }
    )
    print(f"[Skill-B] ✅ 完成: {repo}/{meeting_dir}")


def _extract_summary_text(brief_md: str) -> str:
    """
    从 pre_brief.md 中提取各成员摘要部分，
    用于邮件正文的纯文本展示（去掉 HTML details 折叠块）。
    """
    lines = brief_md.splitlines()
    result = []
    skip = False
    for line in lines:
        if "<details>" in line:
            skip = True
            continue
        if "</details>" in line:
            skip = False
            continue
        if skip:
            continue
        # 去掉 HTML 标签
        if line.startswith("<"):
            continue
        result.append(line)
    return "\n".join(result[:80])  # 邮件正文最多取前 80 行


def main():
    print(f"[Skill-B] 开始扫描 {now_beijing().isoformat()}")

    pending = scan_pending_briefs()
    print(f"[Skill-B] 找到 {len(pending)} 个待处理会议")

    for item in pending:
        try:
            process_meeting(item)
        except Exception as e:
            repo        = item.get("repo", "unknown")
            meeting_dir = item.get("meeting_dir", "unknown")
            print(f"[Skill-B] 处理失败 {repo}/{meeting_dir}: {e}")
            write_log("skill-b", repo, meeting_dir, "brief-error", "error",
                      {"error": str(e)})

    print(f"[Skill-B] 本轮扫描完成")


if __name__ == "__main__":
    main()