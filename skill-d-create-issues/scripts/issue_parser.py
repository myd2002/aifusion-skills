"""
issue_parser.py
解析 confirmed_issue.md 文件内容，提取结构化 issue 列表。

confirmed_issue.md 与 draft_issue.md 格式完全相同（组织者只改文件名，
也可能修改内容），因此解析逻辑针对 draft_writer.py 生成的格式设计，
同时对组织者的手动修改保持容错。

每条 issue 解析结果：
{
  "local_id":   int,
  "task":       str,
  "assignee":   str,   # Gitea 用户名，空字符串表示未分配
  "due_date":   str,   # YYYY-MM-DD，空字符串表示未设置
  "depends_on": [int], # local_id 列表
  "quote":      str    # 原话引用
}

解析失败（格式严重错误）返回 None，由调用方通知组织者。
解析成功但无 action item 返回空列表 []。
"""

import re


# ── 正则模式 ──────────────────────────────────────────────

# 匹配章节标题：### #1 任务描述  或  ### #1 任务描述（组织者可能修改了任务名）
RE_SECTION = re.compile(r'^###\s+#(\d+)\s+(.*)', re.MULTILINE)

# 匹配字段行
RE_ASSIGNEE  = re.compile(r'\*\*负责人\*\*[：:]\s*@?(\S+)')
RE_DUE       = re.compile(r'\*\*截止日期\*\*[：:]\s*(\S+)')
RE_DEPENDS   = re.compile(r'\*\*依赖\*\*[：:]\s*(.*)')
RE_QUOTE     = re.compile(r'>\s+(.*)')

# 无效值标记（组织者未填或待确认时的占位文字）
INVALID_VALUES = {
    "无", "none", "null", "n/a", "na", "待分配", "待确认",
    "⚠️待分配（未识别到负责人）", "⚠️待确认", ""
}


def _clean(s: str) -> str:
    return s.strip().strip("⚠️").strip()


def _parse_depends(raw: str) -> list[int]:
    """
    解析依赖字段，返回 local_id 整数列表。
    支持格式：无 / #1 / #1、#2 / 1,2 / 1 2
    """
    raw = raw.strip()
    if raw.lower() in ("无", "none", "null", ""):
        return []
    numbers = re.findall(r'\d+', raw)
    return [int(n) for n in numbers]


def _parse_section(local_id: int, task: str, body: str) -> dict:
    """解析单条 issue 的字段，body 为该 ### 块下的正文。"""
    assignee  = ""
    due_date  = ""
    depends_on = []
    quote     = ""

    # 负责人
    m = RE_ASSIGNEE.search(body)
    if m:
        val = _clean(m.group(1))
        if val.lower() not in INVALID_VALUES:
            assignee = val

    # 截止日期
    m = RE_DUE.search(body)
    if m:
        val = _clean(m.group(1))
        # 验证格式 YYYY-MM-DD
        if re.match(r'^\d{4}-\d{2}-\d{2}$', val):
            due_date = val

    # 依赖
    m = RE_DEPENDS.search(body)
    if m:
        depends_on = _parse_depends(m.group(1))

    # 原话引用（取第一个 > 引用块）
    m = RE_QUOTE.search(body)
    if m:
        quote = _clean(m.group(1))

    return {
        "local_id":   local_id,
        "task":       task.strip(),
        "assignee":   assignee,
        "due_date":   due_date,
        "depends_on": depends_on,
        "quote":      quote
    }


def parse_confirmed_issue(content: str) -> list[dict] | None:
    """
    解析 confirmed_issue.md 全文。

    返回：
      list[dict]  — 解析成功（含零条到多条 issue）
      None        — 文件格式严重错误，无法解析
    """
    if not content or not content.strip():
        return None

    # 找出所有 ### #N 章节
    sections = list(RE_SECTION.finditer(content))

    # 如果完全没有章节，检查是否是"无 action item"的说明文件
    if not sections:
        # draft_issue.md 在无 action item 时写的是说明文字，不是 None
        if "未提取到明确的行动项" in content or "Action Items" not in content:
            return []
        # 有 Action Items 标题但无 ### 章节，格式异常
        return None

    items = []
    for i, match in enumerate(sections):
        local_id = int(match.group(1))
        task     = match.group(2).strip()

        # 提取该章节正文（到下一个章节前）
        start = match.end()
        end   = sections[i + 1].start() if i + 1 < len(sections) else len(content)
        body  = content[start:end]

        parsed = _parse_section(local_id, task, body)
        items.append(parsed)

    return items