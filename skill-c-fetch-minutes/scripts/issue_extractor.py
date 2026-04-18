"""
issue_extractor.py
MiniMax 两阶段抽取，从会议转录/AI纪要中提取结构化 issue 草稿。

阶段 1：结构化抽取
  输入：ai_summary（优先）+ transcript
  输出：JSON，包含 decisions / action_items / open_questions / notes
  约束：严禁编造转录里没有的内容

阶段 2：字段映射与原话回填
  - assignee_hint → Gitea 用户名
  - due_date_hint → YYYY-MM-DD
  - 在 transcript 全文中搜索最相近的原话作为 quote
  - 仅当原文有明确依赖表达时才设置 depends_on

对外暴露：
  extract_issues(content, attendees, repo) -> dict
  返回结构：
  {
    "decisions":     [str],
    "action_items":  [ActionItem],
    "open_questions":[str],
    "notes":         str
  }

  ActionItem 结构：
  {
    "local_id":    int,
    "task":        str,
    "assignee":    str,     # Gitea 用户名，映射失败为 ""
    "due_date":    str,     # YYYY-MM-DD，解析失败为 ""
    "depends_on":  [int],   # local_id 列表
    "quote":       str      # 转录原话
  }
"""

import os
import json
import re
import requests
import datetime
import difflib

MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_API_URL = "https://api.minimax.chat/v1/text/chatcompletion_v2"

# ── 阶段 1 Prompt ─────────────────────────────────────────

STAGE1_SYSTEM = """你是一个科研团队会议助手，负责从会议记录中提取结构化信息。

请严格按照以下 JSON 格式输出，不要输出任何其他内容：
{
  "decisions": ["决策1", "决策2"],
  "action_items": [
    {
      "local_id": 1,
      "task": "任务描述",
      "assignee_hint": "负责人姓名或称呼（原文中出现的）",
      "due_date_hint": "截止时间描述（原文中出现的，如'下周五'）",
      "depends_on_hint": "依赖描述（原文有明确依赖表达才填，否则填null）",
      "quote": "原文中最能支持此任务的一句话（原话，不要改写）"
    }
  ],
  "open_questions": ["待讨论问题1"],
  "notes": "其他值得记录的信息（单个字符串，无则为空字符串）"
}

【绝对禁止】：
- 禁止编造任何转录原文中没有出现的任务、决策或人名
- 若任务描述含糊（如"继续推进"但无具体内容），宁可跳过不写
- quote 字段必须是原文的实际句子，不得改写或总结
- 若无法找到支持某任务的原话，该条 action_item 整体跳过"""

STAGE1_USER_TEMPLATE = """请从以下会议内容中提取结构化信息。

【AI 智能纪要】（优先参考）：
{ai_summary}

【转录原文】（用于验证和原话回填）：
{transcript}

请输出 JSON："""


# ── 阶段 2：字段映射 ──────────────────────────────────────

def _map_assignee(assignee_hint: str, attendees: list[str]) -> str:
    """
    把 assignee_hint（原文中的称呼）映射到 Gitea 用户名。
    策略：
    1. 精确匹配
    2. 包含匹配（用户名出现在 hint 中，或 hint 出现在用户名中）
    3. difflib 模糊匹配，阈值 0.6
    4. 均失败返回空字符串
    """
    if not assignee_hint:
        return ""

    hint_lower = assignee_hint.lower().strip()

    # 精确匹配
    for username in attendees:
        if username.lower() == hint_lower:
            return username

    # 包含匹配
    for username in attendees:
        uname_lower = username.lower()
        if uname_lower in hint_lower or hint_lower in uname_lower:
            return username

    # 模糊匹配
    matches = difflib.get_close_matches(
        hint_lower,
        [u.lower() for u in attendees],
        n=1,
        cutoff=0.6
    )
    if matches:
        matched_lower = matches[0]
        for username in attendees:
            if username.lower() == matched_lower:
                return username

    return ""


def _parse_due_date(due_date_hint: str,
                    reference_now: datetime.datetime = None) -> str:
    """
    把自然语言截止时间转换为 YYYY-MM-DD。
    失败返回空字符串。
    """
    if not due_date_hint:
        return ""

    import pytz
    TZ = pytz.timezone("Asia/Shanghai")
    now = reference_now or datetime.datetime.now(TZ)

    # 预处理中文关键词
    hint = due_date_hint.strip()
    replacements = {
        "下周五": (now + _next_weekday(now, 4)).strftime("%Y-%m-%d"),
        "下周四": (now + _next_weekday(now, 3)).strftime("%Y-%m-%d"),
        "下周三": (now + _next_weekday(now, 2)).strftime("%Y-%m-%d"),
        "下周二": (now + _next_weekday(now, 1)).strftime("%Y-%m-%d"),
        "下周一": (now + _next_weekday(now, 0)).strftime("%Y-%m-%d"),
        "本周五": (now + _this_weekday(now, 4)).strftime("%Y-%m-%d"),
        "本周四": (now + _this_weekday(now, 3)).strftime("%Y-%m-%d"),
        "本周三": (now + _this_weekday(now, 2)).strftime("%Y-%m-%d"),
        "本周二": (now + _this_weekday(now, 1)).strftime("%Y-%m-%d"),
        "明天":   (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
        "后天":   (now + datetime.timedelta(days=2)).strftime("%Y-%m-%d"),
    }
    for zh, date_str in replacements.items():
        if zh in hint:
            return date_str

    # 尝试 dateutil 解析
    try:
        from dateutil.parser import parse as dateutil_parse
        dt = dateutil_parse(hint, default=now.replace(tzinfo=None),
                            dayfirst=False, yearfirst=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def _next_weekday(now: datetime.datetime, weekday: int) -> datetime.timedelta:
    """距离下一个指定星期几的天数差（weekday: 0=周一, 4=周五）。"""
    days_ahead = weekday - now.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return datetime.timedelta(days=days_ahead + 7)  # "下周"固定+7


def _this_weekday(now: datetime.datetime, weekday: int) -> datetime.timedelta:
    days_ahead = weekday - now.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return datetime.timedelta(days=days_ahead)


def _find_best_quote(task: str, hint_quote: str, transcript: str) -> str:
    """
    在转录全文中搜索与 hint_quote 最相近的原句作为最终 quote。
    策略：
    1. 优先用阶段1提供的 quote（已经是原话）做验证
    2. 在 transcript 按句子分割后做 difflib 最相似匹配
    3. 相似度低于 0.3 时，退回用 hint_quote 本身
    """
    if not transcript:
        return hint_quote or ""

    # 按句子分割转录（支持中英文标点）
    sentences = re.split(r'[。！？.!?\n]+', transcript)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 5]

    if not sentences:
        return hint_quote or ""

    query = hint_quote or task
    if not query:
        return ""

    matches = difflib.get_close_matches(
        query,
        sentences,
        n=1,
        cutoff=0.3
    )
    if matches:
        return matches[0]

    # difflib 找不到时，用简单关键词重叠搜索
    query_words = set(query)
    best_sentence = ""
    best_overlap = 0
    for sent in sentences:
        overlap = len(set(sent) & query_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_sentence = sent

    return best_sentence if best_overlap > 2 else (hint_quote or "")


def _build_depends_on(hint: str | None,
                      all_items: list[dict]) -> list[int]:
    """
    解析依赖关系，返回被依赖的 local_id 列表。
    只有 hint 非空且不为 null 时才处理。
    """
    if not hint or hint.lower() in ("null", "none", "无", ""):
        return []

    deps = []
    # 在 hint 中查找数字（任务编号）
    numbers = re.findall(r'\d+', hint)
    for n in numbers:
        lid = int(n)
        if any(item.get("local_id") == lid for item in all_items):
            deps.append(lid)

    return deps


# ── MiniMax 调用 ──────────────────────────────────────────

def _call_minimax(system_prompt: str, user_content: str,
                  max_tokens: int = 2000) -> str | None:
    if not MINIMAX_API_KEY:
        print("[issue_extractor] 缺少 MINIMAX_API_KEY")
        return None

    payload = {
        "model": "MiniMax-Text-01",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content}
        ],
        "temperature": 0.1,
        "max_tokens":  max_tokens
    }
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type":  "application/json"
    }
    try:
        resp = requests.post(MINIMAX_API_URL, headers=headers,
                             json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[issue_extractor] MiniMax 调用失败: {e}")
        return None


def _parse_json_response(text: str) -> dict | None:
    """安全解析 MiniMax 返回的 JSON，去掉可能的 markdown 代码块。"""
    if not text:
        return None
    # 去掉 ```json ... ``` 包裹
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[issue_extractor] JSON 解析失败: {e}")
        print(f"[issue_extractor] 原始文本: {text[:300]}")
        return None


# ── 对外主入口 ────────────────────────────────────────────

def extract_issues(content: dict, attendees: list[str]) -> dict:
    """
    两阶段抽取。

    content: transcript_fetcher 返回的 dict
      {source, ai_summary, transcript}
    attendees: 参会人员 Gitea 用户名列表

    返回：
    {
      "decisions":     [str],
      "action_items":  [dict],  # 见模块顶部 ActionItem 结构
      "open_questions": [str],
      "notes":         str,
      "source":        str      # 透传 content["source"]
    }
    """
    ai_summary = content.get("ai_summary") or ""
    transcript = content.get("transcript") or ""
    source     = content.get("source", "transcript_only")

    # ── 阶段 1：结构化抽取 ────────────────────────────────
    print("[issue_extractor] 阶段 1：结构化抽取...")
    user_content = STAGE1_USER_TEMPLATE.format(
        ai_summary=ai_summary[:4000] if ai_summary else "（无 AI 智能纪要）",
        transcript=transcript[:4000] if transcript else "（无转录原文）"
    )

    raw = _call_minimax(STAGE1_SYSTEM, user_content, max_tokens=2000)
    stage1 = _parse_json_response(raw)

    if not stage1:
        print("[issue_extractor] 阶段 1 失败，返回空结果")
        return {
            "decisions":      [],
            "action_items":   [],
            "open_questions": [],
            "notes":          "（会议内容抽取失败，请手动整理）",
            "source":         source
        }

    raw_items = stage1.get("action_items", [])

    # ── 阶段 2：字段映射与原话回填 ────────────────────────
    print(f"[issue_extractor] 阶段 2：字段映射，共 {len(raw_items)} 条 action_item...")
    mapped_items = []
    for idx, item in enumerate(raw_items):
        task          = item.get("task", "").strip()
        assignee_hint = item.get("assignee_hint", "")
        due_hint      = item.get("due_date_hint", "")
        depends_hint  = item.get("depends_on_hint", "")
        quote_hint    = item.get("quote", "")

        if not task:
            continue

        assignee   = _map_assignee(assignee_hint, attendees)
        due_date   = _parse_due_date(due_hint)
        quote      = _find_best_quote(task, quote_hint, transcript)
        depends_on = _build_depends_on(depends_hint, raw_items)

        mapped_items.append({
            "local_id":   item.get("local_id", idx + 1),
            "task":       task,
            "assignee":   assignee,
            "due_date":   due_date,
            "depends_on": depends_on,
            "quote":      quote
        })

    return {
        "decisions":      stage1.get("decisions", []),
        "action_items":   mapped_items,
        "open_questions": stage1.get("open_questions", []),
        "notes":          stage1.get("notes", ""),
        "source":         source
    }