"""
issue_creator.py
批量调 Gitea API 在对应仓库创建 issue，并在第二轮回填 depends-on 关系。

第一轮：
  按 local_id 顺序逐条创建 issue，记录 local_id → issue_number 映射。
  每条 issue 的标签：[meeting-action, meeting:YYYY-MM-DD-HHMM]
  正文包含：任务描述 / 负责人 / 截止日期 / 原话引用 / 会议链接

第二轮：
  遍历有 depends_on 的 issue，把依赖的 issue_number 追加到正文。
  失败只记录警告，不影响 issue 本身。

对外暴露：
  create_issues_for_meeting(repo, meeting_dir, meta, items)
    -> (created_issues, failed_items)

  created_issues: [{"local_id": int, "issue_number": int, "title": str}]
  failed_items:   [{"local_id": int, "task": str, "error": str}]
"""

import os
import requests
import datetime

GITEA_BASE_URL  = os.environ.get("GITEA_BASE_URL", "http://43.156.243.152:3000")
GITEA_TOKEN_BOT = os.environ.get("GITEA_TOKEN_BOT", "")

LABEL_MEETING_ACTION = "meeting-action"


def _headers() -> dict:
    return {
        "Authorization": f"token {GITEA_TOKEN_BOT}",
        "Content-Type":  "application/json"
    }


# ── Label 管理 ────────────────────────────────────────────

def _ensure_label(repo: str, label_name: str, color: str = "#0075ca") -> int | None:
    """
    确保仓库中存在指定 label，不存在则创建。
    返回 label id，失败返回 None。
    """
    owner, reponame = repo.split("/", 1)

    # 查询现有 labels
    url = f"{GITEA_BASE_URL}/api/v1/repos/{owner}/{reponame}/labels"
    resp = requests.get(url, headers=_headers(), timeout=10)
    if resp.status_code == 200:
        for label in resp.json():
            if label["name"] == label_name:
                return label["id"]

    # 不存在则创建
    resp = requests.post(
        url,
        headers=_headers(),
        json={"name": label_name, "color": color},
        timeout=10
    )
    if resp.status_code in (200, 201):
        return resp.json().get("id")
    print(f"[issue_creator] 创建 label 失败: {label_name} {resp.text[:100]}")
    return None


def _get_or_create_labels(repo: str, meeting_dir: str) -> list[int]:
    """
    获取或创建本次会议所需的两个 label 的 id 列表：
    - meeting-action（蓝色）
    - meeting:YYYY-MM-DD-HHMM（灰色）
    """
    label_ids = []

    id1 = _ensure_label(repo, LABEL_MEETING_ACTION, "#0075ca")
    if id1:
        label_ids.append(id1)

    meeting_label = f"meeting:{meeting_dir}"
    id2 = _ensure_label(repo, meeting_label, "#e4e669")
    if id2:
        label_ids.append(id2)

    return label_ids


# ── Issue 正文渲染 ────────────────────────────────────────

def _render_issue_body(item: dict, meeting_dir: str,
                       repo: str, join_url: str) -> str:
    """渲染单条 issue 的正文 markdown。"""
    task       = item["task"]
    assignee   = item["assignee"]
    due_date   = item["due_date"]
    quote      = item["quote"]

    assignee_str = f"@{assignee}" if assignee else "待分配"
    due_str      = due_date if due_date else "待确认"
    quote_str    = f"\n> {quote}" if quote else ""

    gitea_base = GITEA_BASE_URL.rstrip("/")
    meeting_url = (f"{gitea_base}/{repo}/src/branch/main/"
                   f"meetings/{meeting_dir}/minutes.md")

    return f"""## 任务描述

{task}

## 基本信息

| 字段 | 内容 |
|------|------|
| 负责人 | {assignee_str} |
| 截止日期 | {due_str} |
| 来源会议 | [{meeting_dir}]({meeting_url}) |
| 入会链接 | {join_url} |

## 原话引用

{quote_str if quote_str else "（未记录原话）"}

---

> 🤖 由 AIFusionBot 根据会议纪要自动创建。请在完成后关闭此 issue。
"""


def _render_depends_comment(depends_on: list[int],
                            id_to_number: dict[int, int]) -> str:
    """生成依赖关系追加文字。"""
    links = []
    for lid in depends_on:
        number = id_to_number.get(lid)
        if number:
            links.append(f"#{number}（任务 #{lid}）")
        else:
            links.append(f"（任务 #{lid}，issue 创建失败）")
    return "**依赖**：" + " / ".join(links)


# ── Gitea API 调用 ────────────────────────────────────────

def _create_single_issue(repo: str, title: str, body: str,
                         assignee: str, due_date: str,
                         label_ids: list[int]) -> dict | None:
    """
    调 Gitea API 创建单条 issue。
    返回创建成功的 issue dict（含 number），失败返回 None。
    """
    owner, reponame = repo.split("/", 1)
    url = f"{GITEA_BASE_URL}/api/v1/repos/{owner}/{reponame}/issues"

    payload = {
        "title":  title,
        "body":   body,
        "labels": label_ids
    }

    if assignee:
        payload["assignees"] = [assignee]

    if due_date:
        # Gitea due_date 格式：RFC3339
        try:
            dt = datetime.datetime.strptime(due_date, "%Y-%m-%d")
            payload["due_date"] = dt.strftime("%Y-%m-%dT23:59:59Z")
        except Exception:
            pass

    try:
        resp = requests.post(url, headers=_headers(), json=payload, timeout=15)
        if resp.status_code in (200, 201):
            return resp.json()
        print(f"[issue_creator] 创建 issue 失败: {resp.status_code} {resp.text[:200]}")
        return None
    except Exception as e:
        print(f"[issue_creator] 创建 issue 异常: {e}")
        return None


def _append_comment(repo: str, issue_number: int, body: str) -> bool:
    """向 issue 追加评论（用于回填依赖关系）。"""
    owner, reponame = repo.split("/", 1)
    url = (f"{GITEA_BASE_URL}/api/v1/repos/{owner}/{reponame}"
           f"/issues/{issue_number}/comments")
    try:
        resp = requests.post(
            url, headers=_headers(), json={"body": body}, timeout=10
        )
        return resp.status_code in (200, 201)
    except Exception as e:
        print(f"[issue_creator] 追加评论失败 #{issue_number}: {e}")
        return False


# ── 对外主入口 ────────────────────────────────────────────

def create_issues_for_meeting(
    repo: str,
    meeting_dir: str,
    meta: dict,
    items: list[dict]
) -> tuple[list[dict], list[dict]]:
    """
    批量创建 Gitea issue（两轮）。

    第一轮：逐条创建，记录 local_id → issue_number 映射
    第二轮：回填 depends-on 关系（追加评论）

    返回：
      created_issues: [{"local_id": int, "issue_number": int, "title": str}]
      failed_items:   [{"local_id": int, "task": str, "error": str}]
    """
    if not items:
        return [], []

    join_url = meta.get("join_url", "")
    topic    = meta.get("topic", "团队会议")

    # 准备 labels
    print(f"[issue_creator] 准备 labels for {repo}/{meeting_dir}")
    label_ids = _get_or_create_labels(repo, meeting_dir)

    created_issues: list[dict] = []
    failed_items:   list[dict] = []
    id_to_number:   dict[int, int] = {}   # local_id → issue_number

    # ── 第一轮：逐条创建 ──────────────────────────────────
    print(f"[issue_creator] 第一轮：创建 {len(items)} 条 issue")
    for item in items:
        local_id = item["local_id"]
        task     = item["task"]
        title    = f"[{meeting_dir}] {task}"
        body     = _render_issue_body(item, meeting_dir, repo, join_url)

        result = _create_single_issue(
            repo=repo,
            title=title,
            body=body,
            assignee=item.get("assignee", ""),
            due_date=item.get("due_date", ""),
            label_ids=label_ids
        )

        if result:
            issue_number = result["number"]
            id_to_number[local_id] = issue_number
            created_issues.append({
                "local_id":     local_id,
                "issue_number": issue_number,
                "title":        title,
                "assignee":     item.get("assignee", ""),
                "due_date":     item.get("due_date", ""),
                "url":          result.get("html_url", "")
            })
            print(f"[issue_creator] ✅ #{issue_number} local_id={local_id} {task[:40]}")
        else:
            failed_items.append({
                "local_id": local_id,
                "task":     task,
                "error":    "Gitea API 创建失败"
            })
            print(f"[issue_creator] ❌ local_id={local_id} {task[:40]}")

    # ── 第二轮：回填 depends-on 关系 ──────────────────────
    print(f"[issue_creator] 第二轮：回填依赖关系")
    for item in items:
        depends_on = item.get("depends_on", [])
        if not depends_on:
            continue

        local_id     = item["local_id"]
        issue_number = id_to_number.get(local_id)
        if not issue_number:
            continue  # 该 issue 创建失败，跳过

        comment_body = _render_depends_comment(depends_on, id_to_number)
        ok = _append_comment(repo, issue_number, comment_body)
        if ok:
            print(f"[issue_creator] 依赖回填成功 #{issue_number}")
        else:
            print(f"[issue_creator] ⚠️ 依赖回填失败 #{issue_number}（非致命）")

    return created_issues, failed_items