"""
notify.py
Skill-D 的邮件通知模块，负责两类邮件：

1. 全员邮件（单项目会议）
   - 正式 minutes 通知
   - 附上本次会议新建的 issue 列表

2. 个人任务通知（单项目会议，每个 assignee 单独发）
   - 只列出分配给该成员的 issue

3. 跨项目会议邮件（仅发给组织者）
   - 不建 issue，仅把 draft_issue.md 内容作为建议发给组织者

对外暴露：
  notify_all(repo, meeting_dir, meta, items,
             created_issues, failed_items, is_cross_project)
"""

import os
from scripts.gitea_ops  import get_user_email, get_all_member_emails, read_file
from scripts.email_sender import send_plain_email
from scripts.common     import GITEA_BASE_URL, ADVISOR_USERNAME


def _get_organizer_email(meta: dict) -> str | None:
    organizer = meta.get("organizer", "")
    email = get_user_email(organizer) if organizer else None
    if not email and ADVISOR_USERNAME:
        email = get_user_email(ADVISOR_USERNAME)
    return email


def _render_issue_list(created_issues: list[dict]) -> str:
    """把 created_issues 渲染为可读的文本列表。"""
    if not created_issues:
        return "（本次会议无行动项）"
    lines = []
    for ci in created_issues:
        assignee_str = f"@{ci['assignee']}" if ci.get("assignee") else "待分配"
        due_str      = f" | 截止：{ci['due_date']}" if ci.get("due_date") else ""
        lines.append(
            f"  #{ci['issue_number']} {ci['title']}"
            f"\n    负责人：{assignee_str}{due_str}"
            f"\n    链接：{ci.get('url', '')}"
        )
    return "\n\n".join(lines)


# ── 1. 全员邮件 ───────────────────────────────────────────

def _send_team_email(repo: str, meeting_dir: str, meta: dict,
                     created_issues: list[dict],
                     failed_items: list[dict]):
    """发送正式 minutes 通知邮件给全员。"""
    topic          = meta.get("topic", "团队会议")
    scheduled_time = meta.get("scheduled_time", "")
    attendees      = meta.get("attendees", [])

    # 收集全员邮箱
    email_map = get_all_member_emails(repo)
    all_emails = list(email_map.values())
    if not all_emails:
        print("[notify] 未找到全员邮箱，跳过全员通知")
        return

    gitea_base   = GITEA_BASE_URL.rstrip("/")
    minutes_url  = (f"{gitea_base}/{repo}/src/branch/main/"
                    f"meetings/{meeting_dir}/minutes.md")

    issue_list_str = _render_issue_list(created_issues)

    failed_str = ""
    if failed_items:
        failed_lines = [f"  - #{fi['local_id']} {fi['task']}" for fi in failed_items]
        failed_str = (
            f"\n\n⚠️ 以下 {len(failed_items)} 条 issue 创建失败，请组织者手动处理：\n"
            + "\n".join(failed_lines)
        )

    subject = f"【会议纪要】{topic} - {scheduled_time}"
    body = f"""您好，

{topic}（{meeting_dir}）的会议纪要已发布，本次会议共创建 {len(created_issues)} 条 issue。

📋 完整纪要：{minutes_url}

---

【本次会议行动项】

{issue_list_str}{failed_str}

---
此邮件由 AIFusionBot 自动发送，请勿直接回复。
"""
    send_plain_email(all_emails, subject, body)
    print(f"[notify] 全员邮件已发送，收件人 {len(all_emails)} 人")


# ── 2. 个人任务通知 ───────────────────────────────────────

def _send_personal_emails(repo: str, meeting_dir: str,
                          meta: dict, created_issues: list[dict]):
    """向每个 assignee 发送个人任务通知邮件。"""
    if not created_issues:
        return

    topic          = meta.get("topic", "团队会议")
    scheduled_time = meta.get("scheduled_time", "")
    gitea_base     = GITEA_BASE_URL.rstrip("/")

    # 按 assignee 分组
    assignee_issues: dict[str, list[dict]] = {}
    for ci in created_issues:
        assignee = ci.get("assignee", "")
        if not assignee:
            continue
        assignee_issues.setdefault(assignee, []).append(ci)

    for assignee, issues in assignee_issues.items():
        email = get_user_email(assignee)
        if not email:
            print(f"[notify] 找不到 {assignee} 的邮箱，跳过个人通知")
            continue

        issue_lines = []
        for ci in issues:
            due_str = f"截止：{ci['due_date']}" if ci.get("due_date") else "截止日期待确认"
            issue_lines.append(
                f"  • #{ci['issue_number']} {ci['title']}\n"
                f"    {due_str}\n"
                f"    {ci.get('url', '')}"
            )
        issue_str = "\n\n".join(issue_lines)

        subject = f"【任务通知】{topic} 为您分配了 {len(issues)} 条任务"
        body = f"""您好 {assignee}，

在 {topic}（{scheduled_time}）中，以下任务已分配给您：

{issue_str}

请在 Gitea 中跟进上述 issue，完成后关闭对应 issue。

📋 完整会议纪要：{gitea_base}/{repo}/src/branch/main/meetings/{meeting_dir}/minutes.md

---
此邮件由 AIFusionBot 自动发送，请勿直接回复。
"""
        send_plain_email([email], subject, body)
        print(f"[notify] 个人通知已发送给 {assignee}（{email}），共 {len(issues)} 条任务")


# ── 3. 跨项目会议邮件 ─────────────────────────────────────

def _send_cross_project_email(repo: str, meeting_dir: str, meta: dict):
    """
    跨项目会议不建 issue，仅把 draft_issue.md 内容发给组织者。
    """
    topic    = meta.get("topic", "团队会议")
    gitea_base = GITEA_BASE_URL.rstrip("/")

    organizer_email = _get_organizer_email(meta)
    if not organizer_email:
        print("[notify] 跨项目会议：找不到组织者邮箱，跳过")
        return

    # 读取 confirmed_issue.md 内容作为建议
    draft_content, _ = read_file(
        repo, f"meetings/{meeting_dir}/confirmed_issue.md"
    )
    draft_str = draft_content if draft_content else "（内容读取失败，请手动查阅）"

    minutes_url = (f"{gitea_base}/{repo}/src/branch/main/"
                   f"meetings/{meeting_dir}/minutes.md")

    subject = f"【跨项目会议纪要】{topic} - issue 建议清单"
    body = f"""您好，

{topic}（{meeting_dir}）为跨项目会议，AIFusionBot 不自动创建 issue。

请参考以下 issue 建议，自行在对应项目仓库手动创建：

📋 完整纪要：{minutes_url}

---

【Issue 建议清单】

{draft_str}

---
此邮件由 AIFusionBot 自动发送，请勿直接回复。
"""
    send_plain_email([organizer_email], subject, body)
    print(f"[notify] 跨项目会议邮件已发送给组织者 {organizer_email}")


# ── 对外主入口 ────────────────────────────────────────────

def notify_all(repo: str, meeting_dir: str, meta: dict,
               items: list[dict], created_issues: list[dict],
               failed_items: list[dict], is_cross_project: bool):
    """
    统一通知入口。
    单项目会议：全员邮件 + 个人任务通知
    跨项目会议：仅给组织者发建议邮件
    """
    if is_cross_project:
        _send_cross_project_email(repo, meeting_dir, meta)
    else:
        _send_team_email(repo, meeting_dir, meta, created_issues, failed_items)
        _send_personal_emails(repo, meeting_dir, meta, created_issues)