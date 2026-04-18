"""
draft_writer.py
把 issue_extractor 的输出渲染成文件并 commit 到 Gitea 会议目录：

产物：
  transcript.md     - 转录原文（溯源依据）
  ai_summary.md     - AI 智能纪要（Layer 1 成功时）
  minutes.md        - 正式会议纪要（decisions / open_questions / notes）
  draft_issue.md    - 待审核 issue 草稿（含勾选框 / 负责人 / 截止日期 / 依赖 / 原话引用）

对外暴露：
  write_meeting_documents(repo, meeting_dir, meta, content, extracted) -> bool
"""

import datetime
from scripts.gitea_ops import create_file, read_file
from scripts.common    import GITEA_BASE_URL, now_beijing


# ── 渲染函数 ──────────────────────────────────────────────

def _render_transcript_md(transcript: str, meeting_dir: str,
                          topic: str, source: str) -> str:
    generated_at = now_beijing().strftime("%Y-%m-%d %H:%M UTC+8")
    source_label = "AI 智能纪要 + 转录原文" if source == "ai_summary" else "仅转录原文"
    return f"""# 会议转录原文

> 会议：{topic}（{meeting_dir}）
> 来源：{source_label}
> 生成时间：{generated_at}

---

{transcript}
"""


def _render_ai_summary_md(ai_summary: str, meeting_dir: str,
                          topic: str) -> str:
    generated_at = now_beijing().strftime("%Y-%m-%d %H:%M UTC+8")
    return f"""# AI 智能纪要

> 会议：{topic}（{meeting_dir}）
> 来源：腾讯会议 AI 智能纪要
> 生成时间：{generated_at}

---

{ai_summary}
"""


def _render_minutes_md(extracted: dict, meeting_dir: str,
                       topic: str, scheduled_time: str,
                       join_url: str, attendees: list[str]) -> str:
    generated_at = now_beijing().strftime("%Y-%m-%d %H:%M UTC+8")
    source_note  = "AI 智能纪要" if extracted["source"] == "ai_summary" else "转录原文"

    # 决策列表
    decisions = extracted.get("decisions", [])
    decisions_md = "\n".join(f"- {d}" for d in decisions) if decisions else "- 暂无"

    # 待讨论问题
    questions = extracted.get("open_questions", [])
    questions_md = "\n".join(f"- {q}" for q in questions) if questions else "- 暂无"

    # 备注
    notes = extracted.get("notes", "") or "暂无"

    # 参会人员
    attendees_md = "\n".join(f"- {a}" for a in attendees) if attendees else "- （未记录）"

    # action items 简表（正文只列标题，详情见 draft_issue.md）
    items = extracted.get("action_items", [])
    if items:
        items_md_lines = []
        for item in items:
            assignee_str = f"@{item['assignee']}" if item["assignee"] else "待分配"
            due_str      = f" 截止：{item['due_date']}" if item["due_date"] else ""
            items_md_lines.append(
                f"- [ ] **#{item['local_id']}** {item['task']}"
                f"　{assignee_str}{due_str}"
            )
        items_md = "\n".join(items_md_lines)
    else:
        items_md = "- 本次会议无明确行动项"

    return f"""# 会议纪要

## 基本信息

- **会议主题**：{topic}
- **会议时间**：{scheduled_time}
- **入会链接**：{join_url}
- **参会人员**：
{attendees_md}
- **纪要生成时间**：{generated_at}
- **内容来源**：{source_note}

---

## 会议决策

{decisions_md}

---

## 行动项（详见 draft_issue.md）

{items_md}

---

## 待讨论问题

{questions_md}

---

## 备注

{notes}

---

> 📌 本纪要由 AIFusionBot 根据会议内容自动生成，请组织者核实后确认。
"""


def _render_draft_issue_md(extracted: dict, meeting_dir: str,
                           topic: str, repo: str,
                           scheduled_time: str) -> str:
    generated_at = now_beijing().strftime("%Y-%m-%d %H:%M UTC+8")
    items = extracted.get("action_items", [])

    if not items:
        return f"""# Issue 草稿（待审核）

> 会议：{topic}（{meeting_dir}）
> 生成时间：{generated_at}

本次会议未提取到明确的行动项。

如需手动创建 issue，请直接在 Gitea 仓库操作。
"""

    gitea_base = GITEA_BASE_URL.rstrip("/")
    confirm_rename_hint = (
        f"`meetings/{meeting_dir}/draft_issue.md` → "
        f"`meetings/{meeting_dir}/confirmed_issue.md`"
    )

    lines = [
        "# Issue 草稿（待审核）",
        "",
        f"> 会议：{topic}（{meeting_dir}）",
        "> 生成时间：{generated_at}",
        f"> 内容来源：{'AI 智能纪要 + 转录' if extracted['source'] == 'ai_summary' else '转录原文'}",
        "",
        "## 使用说明",
        "",
        "请组织者审核以下 issue 草稿，确认无误后选择以下任一方式触发创建：",
        "",
        f"**方式 A（Gitea 网页）**：将本文件重命名为 `confirmed_issue.md`",
        f"> 路径：{confirm_rename_hint}",
        "",
        f'**方式 B（OpenClaw 对话）**：说"确认 `{meeting_dir}` 的 issue"',
        "",
        "---",
        "",
        "## Action Items",
        ""
    ]

    for item in items:
        local_id   = item["local_id"]
        task       = item["task"]
        assignee   = item["assignee"]
        due_date   = item["due_date"]
        depends_on = item["depends_on"]
        quote      = item["quote"]

        assignee_str  = f"@{assignee}" if assignee else "⚠️ 待分配（未识别到负责人）"
        due_str       = due_date if due_date else "⚠️ 待确认"
        depends_str   = (
            "、".join(f"#{d}" for d in depends_on)
            if depends_on else "无"
        )

        lines += [
            f"### #{local_id} {task}",
            "",
            f"- **负责人**：{assignee_str}",
            f"- **截止日期**：{due_str}",
            f"- **依赖**：{depends_str}",
            f"- **原话引用**：",
            f"  > {quote}" if quote else "  > （未找到原话，请手动补充）",
            "",
            "---",
            ""
        ]

    lines += [
        "> ⚠️ AI 生成内容，请仔细核对负责人、截止日期与依赖关系后再确认。",
        "> 如有问题请直接编辑本文件后再重命名确认。",
        ""
    ]

    return "\n".join(lines)


# ── 对外主入口 ────────────────────────────────────────────

def write_meeting_documents(repo: str, meeting_dir: str,
                            meta: dict, content: dict,
                            extracted: dict) -> bool:
    """
    把所有产物文件 commit 到 Gitea 会议目录。
    已存在的文件跳过创建（幂等）。
    返回 True 表示 draft_issue.md 成功写入（核心产物）。
    """
    topic          = meta.get("topic", "团队会议")
    scheduled_time = meta.get("scheduled_time", "")
    join_url       = meta.get("join_url", "")
    attendees      = meta.get("attendees", [])

    ai_summary  = content.get("ai_summary")
    transcript  = content.get("transcript", "")
    source      = content.get("source", "transcript_only")

    base_path = f"meetings/{meeting_dir}"

    # ── 1. transcript.md ──────────────────────────────────
    _safe_create(
        repo,
        f"{base_path}/transcript.md",
        _render_transcript_md(transcript, meeting_dir, topic, source),
        f"feat: add transcript for {meeting_dir}"
    )

    # ── 2. ai_summary.md（仅 Layer 1 成功时）──────────────
    if ai_summary:
        _safe_create(
            repo,
            f"{base_path}/ai_summary.md",
            _render_ai_summary_md(ai_summary, meeting_dir, topic),
            f"feat: add ai_summary for {meeting_dir}"
        )

    # ── 3. minutes.md ─────────────────────────────────────
    _safe_create(
        repo,
        f"{base_path}/minutes.md",
        _render_minutes_md(extracted, meeting_dir, topic,
                           scheduled_time, join_url, attendees),
        f"feat: add minutes for {meeting_dir}"
    )

    # ── 4. draft_issue.md（核心产物）─────────────────────
    draft_content = _render_draft_issue_md(
        extracted, meeting_dir, topic, repo, scheduled_time
    )
    ok = _safe_create(
        repo,
        f"{base_path}/draft_issue.md",
        draft_content,
        f"feat: add draft_issue for {meeting_dir}"
    )

    return ok


def _safe_create(repo: str, path: str, content: str,
                 commit_msg: str) -> bool:
    """
    检查文件是否已存在，不存在才创建（幂等）。
    返回 True 表示文件已存在或创建成功。
    """
    existing, _ = read_file(repo, path)
    if existing is not None:
        print(f"[draft_writer] 跳过已存在文件: {path}")
        return True
    ok = create_file(repo, path, content, commit_msg)
    if not ok:
        print(f"[draft_writer] 创建失败: {path}")
    return ok