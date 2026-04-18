"""
brief_generator.py
基于 activity_fetcher 拉取的活动数据，调用 MiniMax 生成每人工作进展摘要，
最终合成 pre_brief.md 文件内容字符串。

MiniMax 摘要生成逻辑直接对标 gitea-routine-report 的 AI 分析部分：
- 输入：commits 消息列表 + issue 标题 + PR 标题 + 未完成 meeting-action
- 输出：3-5 条 bullet point，严禁编造
- 与 routine-report 不同之处：输出格式为 markdown（而非 HTML 邮件），
  且额外展示未完成的 meeting-action 列表
"""

import os
import json
import requests
import datetime

MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_API_URL = "https://api.minimax.chat/v1/text/chatcompletion_v2"

MEMBER_SUMMARY_SYSTEM = """你是一个科研团队会议助手，负责在会议前生成每位成员的工作进展摘要。
根据提供的 Gitea 活动数据（commit 记录、issue、PR），用 3-5 条 bullet point 总结该成员的工作进展。

要求：
1. 每条 bullet point 不超过 40 字
2. 优先描述实质性进展，而非泛泛的"更新了代码"
3. 严禁编造原始数据中没有的内容
4. 若活动数据为空，输出"本周期内暂无 Gitea 活动记录"
5. 只输出 bullet point 列表，每条以"- "开头，不要标题和前言"""


def _build_member_context(username: str, activity: dict,
                          since: datetime.datetime, until: datetime.datetime) -> str:
    """把一个成员的活动数据序列化为给 MiniMax 的文本上下文。"""
    lines = [f"成员：{username}",
             f"统计窗口：{since.strftime('%Y-%m-%d %H:%M')} 至 {until.strftime('%Y-%m-%d %H:%M')}（北京时间）",
             ""]

    # Commits
    commits = activity.get("commits", [])
    if commits:
        lines.append(f"【Commits】共 {len(commits)} 次")
        for c in commits[:15]:  # 最多取 15 条避免超出 token
            vague_mark = "（描述模糊）" if c.get("is_vague") else ""
            lines.append(f"  - [{c['time']}] {c['message']}{vague_mark}"
                         f"（+{c['stats']['additions']}/-{c['stats']['deletions']} 行，"
                         f"{c['stats']['files_changed']} 个文件，分支：{c['branch']}）")
    else:
        lines.append("【Commits】本周期无提交")

    lines.append("")

    # Issues
    issues = activity.get("issues", [])
    if issues:
        lines.append(f"【Issues】共 {len(issues)} 条")
        for i in issues[:10]:
            lines.append(f"  - #{i['number']} {i['title']}（{i['type']}）")
    else:
        lines.append("【Issues】本周期无相关 issue")

    lines.append("")

    # PRs
    prs = activity.get("prs", [])
    if prs:
        lines.append(f"【Pull Requests】共 {len(prs)} 个")
        for pr in prs[:10]:
            lines.append(f"  - #{pr['number']} {pr['title']}")
    else:
        lines.append("【Pull Requests】本周期无 PR")

    return "\n".join(lines)


def generate_member_summary(username: str, activity: dict,
                            since: datetime.datetime, until: datetime.datetime) -> str:
    """
    调用 MiniMax 为单个成员生成 3-5 条 bullet point 工作进展摘要。
    失败时返回降级文字。
    """
    if not MINIMAX_API_KEY:
        return "- （MiniMax API 未配置，无法生成摘要）"

    context = _build_member_context(username, activity, since, until)

    payload = {
        "model": "MiniMax-Text-01",
        "messages": [
            {"role": "system", "content": MEMBER_SUMMARY_SYSTEM},
            {"role": "user",   "content": context}
        ],
        "temperature": 0.2,
        "max_tokens": 400
    }
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(MINIMAX_API_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        return text
    except Exception as e:
        print(f"[brief_generator] MiniMax 调用失败 username={username}: {e}")
        # 降级：直接列出 commit messages
        commits = activity.get("commits", [])
        if not commits:
            return "- 本周期内暂无 Gitea 活动记录"
        bullets = [f"- {c['message'][:60]}" for c in commits[:5]]
        return "\n".join(bullets)


def render_pre_brief_md(topic: str, meeting_dir: str, scheduled_time: str,
                        join_url: str, meeting_code: str,
                        since: datetime.datetime, until: datetime.datetime,
                        activities: dict[str, dict],
                        summaries: dict[str, str]) -> str:
    """
    合成完整的 pre_brief.md 内容字符串。

    activities: {username: activity_dict}  来自 activity_fetcher
    summaries:  {username: bullet_points}  来自 generate_member_summary
    """
    since_str = since.strftime("%Y-%m-%d %H:%M")
    until_str = until.strftime("%Y-%m-%d %H:%M")
    generated_at = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))
    ).strftime("%Y-%m-%d %H:%M UTC+8")

    lines = [
        f"# 会前简报",
        "",
        f"## 基本信息",
        "",
        f"- **会议主题**：{topic}",
        f"- **会议时间**：{scheduled_time}",
        f"- **会议号**：{meeting_code}",
        f"- **入会链接**：{join_url}",
        f"- **简报生成时间**：{generated_at}",
        f"- **活动统计窗口**：{since_str} 至 {until_str}（北京时间）",
        "",
        "---",
        "",
        "## 各成员工作进展",
        "",
    ]

    for username, activity in activities.items():
        commit_count = activity.get("commit_count", 0)
        additions    = activity.get("total_additions", 0)
        deletions    = activity.get("total_deletions", 0)
        ft           = activity.get("file_type_summary", {})
        pending      = activity.get("pending_actions", [])
        summary_text = summaries.get(username, "- 暂无摘要")

        # 活跃度标签（与 render_email.py 保持一致）
        if commit_count >= 3:
            activity_label = "🟢 活跃"
        elif commit_count >= 1:
            activity_label = "🟡 正常"
        else:
            activity_label = "🔴 本周期无提交"

        lines += [
            f"### 👤 {username}　{activity_label}",
            "",
            f"**提交统计**：{commit_count} 次提交 / +{additions} -{deletions} 行"
            + (f" / 代码 {ft.get('code',0)} 文档 {ft.get('doc',0)} 数据 {ft.get('data',0)} 个文件"
               if ft else ""),
            "",
            "**工作进展摘要（AI 生成）**：",
            "",
        ]
        lines += [f"{summary_text}", ""]

        # 未完成 meeting-action
        if pending:
            lines.append("**⏳ 待完成任务（上次会议派发）**：")
            lines.append("")
            for p in pending:
                due_str = f"（截止：{p['due']}）" if p.get("due") else ""
                lines.append(f"- [ ] #{p['number']} {p['title']}{due_str} → {p['url']}")
            lines.append("")

        # 本周期 commits 列表（折叠呈现）
        commits = activity.get("commits", [])
        if commits:
            lines.append("<details>")
            lines.append(f"<summary>📝 Commits 明细（共 {len(commits)} 条，点击展开）</summary>")
            lines.append("")
            for c in commits:
                vague = " ⚠️模糊描述" if c.get("is_vague") else ""
                lines.append(f"- `{c['sha']}` [{c['time']}] **{c['branch']}** {c['message']}{vague}")
            lines.append("")
            lines.append("</details>")
            lines.append("")

        lines.append("---")
        lines.append("")

    lines += [
        "> 📌 本简报由 AIFusionBot 自动生成，AI 摘要仅供参考，以实际 Gitea 记录为准。",
        ""
    ]

    return "\n".join(lines)


def generate_brief(topic: str, meeting_dir: str, scheduled_time: str,
                   join_url: str, meeting_code: str,
                   since: datetime.datetime, until: datetime.datetime,
                   activities: dict[str, dict]) -> str:
    """
    对外主入口：传入活动数据，返回完整的 pre_brief.md 字符串。
    内部依次调用 MiniMax 为每个成员生成摘要，然后合成 markdown。
    """
    summaries = {}
    for username, activity in activities.items():
        print(f"[brief_generator] 生成 {username} 的摘要...")
        summaries[username] = generate_member_summary(username, activity, since, until)

    return render_pre_brief_md(
        topic=topic,
        meeting_dir=meeting_dir,
        scheduled_time=scheduled_time,
        join_url=join_url,
        meeting_code=meeting_code,
        since=since,
        until=until,
        activities=activities,
        summaries=summaries
    )