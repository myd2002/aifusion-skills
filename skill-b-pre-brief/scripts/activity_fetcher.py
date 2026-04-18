"""
activity_fetcher.py
拉取每个成员在指定时间窗口内的 Gitea 活动数据：
- commits（复用 gitea-routine-report 的 get_commits_by_repo 逻辑）
- issues（该成员创建或被分配的）
- PRs
- 未完成的 meeting-action issue（label=meeting-action, state=open, assignee=该成员）

对外暴露：
  fetch_member_activities(repo, username, since, until) -> dict
  fetch_all_members_activities(repo, attendees, since, until) -> dict[username -> dict]
"""

import os
import datetime
import requests
from datetime import timezone, timedelta

GITEA_BASE_URL  = os.environ.get("GITEA_BASE_URL", "http://43.156.243.152:3000")
GITEA_TOKEN_BOT = os.environ.get("GITEA_TOKEN_BOT", "")

UTC_PLUS_8 = timezone(timedelta(hours=8))

VAGUE_KEYWORDS = [
    "update", "fix", "modify", "change", "edit", "adjust",
    "修改", "更新", "修复", "调整", "改", "完善", "优化", "test", "测试"
]


def _headers():
    return {"Authorization": f"token {GITEA_TOKEN_BOT}"}


def _utc(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC_PLUS_8)
    return dt.astimezone(timezone.utc)


def _is_vague(message: str) -> bool:
    msg = message.strip().lower()
    if len(msg) < 10:
        return True
    for kw in VAGUE_KEYWORDS:
        if msg in [kw, kw + ".", kw + "s"]:
            return True
    return False


def _fmt_time(iso_str: str) -> str:
    try:
        dt = datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(UTC_PLUS_8).strftime("%Y-%m-%d %H:%M UTC+8")
    except Exception:
        return iso_str


# ── Commits（复用 gitea-routine-report 核心逻辑）────────────

def _get_branch_sha_map(repo_full_name: str, since: datetime.datetime) -> dict:
    """建立 sha -> branch_name 映射，逻辑直接复用 gitea-routine-report。"""
    sha_to_branch = {}
    url = f"{GITEA_BASE_URL}/api/v1/repos/{repo_full_name}/branches"
    resp = requests.get(url, headers=_headers(), params={"limit": 50}, timeout=10)
    if resp.status_code != 200:
        return sha_to_branch

    for branch in resp.json():
        branch_name = branch["name"]
        commits_url = f"{GITEA_BASE_URL}/api/v1/repos/{repo_full_name}/commits"
        params = {
            "sha": branch_name,
            "limit": 50,
            "since": _utc(since).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        br = requests.get(commits_url, headers=_headers(), params=params, timeout=10)
        if br.status_code == 200:
            for commit in br.json():
                sha = commit["sha"]
                if sha not in sha_to_branch:
                    sha_to_branch[sha] = branch_name
                elif branch_name not in ("main", "master"):
                    sha_to_branch[sha] = branch_name

    return sha_to_branch


def _fetch_commits(repo_full_name: str, username: str,
                   since: datetime.datetime, until: datetime.datetime) -> list:
    """
    拉取指定成员在时间窗口内的 commits，结构与 gitea-routine-report 一致。
    """
    since_utc = _utc(since)
    until_utc = _utc(until)

    sha_to_branch = _get_branch_sha_map(repo_full_name, since)

    url = f"{GITEA_BASE_URL}/api/v1/repos/{repo_full_name}/commits"
    params = {
        "limit": 50,
        "page": 1,
        "since": since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    }
    resp = requests.get(url, headers=_headers(), params=params, timeout=10)
    if resp.status_code != 200:
        return []

    result = []
    for commit in resp.json():
        # 过滤时间
        raw_time = commit["commit"]["author"]["date"]
        try:
            ct = datetime.datetime.fromisoformat(raw_time.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            continue
        if ct < since_utc or ct > until_utc:
            continue

        # 过滤作者（按 Gitea 用户名匹配 commit author name，容忍大小写）
        author_name = commit["commit"]["author"]["name"]
        committer_login = (commit.get("author") or {}).get("login", "")
        if committer_login.lower() != username.lower() and author_name.lower() != username.lower():
            continue

        sha = commit["sha"]
        message = commit["commit"]["message"].strip()

        # 拉取文件变动详情
        diff_url = f"{GITEA_BASE_URL}/api/v1/repos/{repo_full_name}/git/commits/{sha}"
        diff_resp = requests.get(diff_url, headers=_headers(), timeout=10)
        files = []
        additions, deletions = 0, 0
        if diff_resp.status_code == 200:
            for f in diff_resp.json().get("files", []):
                additions += f.get("additions", 0)
                deletions += f.get("deletions", 0)
                files.append({
                    "filename": f.get("filename", ""),
                    "status":   f.get("status", ""),
                    "additions": f.get("additions", 0),
                    "deletions": f.get("deletions", 0)
                })

        result.append({
            "sha":      sha[:8],
            "author":   author_name,
            "time":     _fmt_time(raw_time),
            "message":  message,
            "is_vague": _is_vague(message),
            "branch":   sha_to_branch.get(sha, "main"),
            "stats":    {"additions": additions, "deletions": deletions, "files_changed": len(files)},
            "files":    files
        })

    return result


# ── Issues ────────────────────────────────────────────────

def _fetch_issues(repo_full_name: str, username: str,
                  since: datetime.datetime, until: datetime.datetime) -> list:
    """拉取该成员在窗口期内创建或被分配的 issues（非 PR）。"""
    since_utc = _utc(since)
    until_utc = _utc(until)
    owner, repo = repo_full_name.split("/", 1)
    result = []

    for query_type in ["created", "assigned"]:
        url = f"{GITEA_BASE_URL}/api/v1/repos/{owner}/{repo}/issues"
        params = {
            "type":  "issues",
            "state": "open",
            "limit": 50,
            "page":  1
        }
        if query_type == "created":
            params["created_by"] = username
        else:
            params["assigned_by"] = username

        resp = requests.get(url, headers=_headers(), params=params, timeout=10)
        if resp.status_code != 200:
            continue

        for issue in resp.json():
            created = issue.get("created", "")
            try:
                ct = datetime.datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(timezone.utc)
                if ct < since_utc or ct > until_utc:
                    continue
            except Exception:
                pass  # 时间解析失败则不过滤，保留

            entry = {
                "number":  issue.get("number"),
                "title":   issue.get("title", ""),
                "state":   issue.get("state", ""),
                "url":     issue.get("html_url", ""),
                "created": _fmt_time(created),
                "type":    query_type
            }
            # 去重
            if not any(e["number"] == entry["number"] for e in result):
                result.append(entry)

    return result


# ── PRs ───────────────────────────────────────────────────

def _fetch_prs(repo_full_name: str, username: str,
               since: datetime.datetime, until: datetime.datetime) -> list:
    """拉取该成员在窗口期内创建的 PR。"""
    since_utc = _utc(since)
    until_utc = _utc(until)
    owner, repo = repo_full_name.split("/", 1)

    url = f"{GITEA_BASE_URL}/api/v1/repos/{owner}/{repo}/pulls"
    params = {"state": "open", "limit": 50, "page": 1}
    resp = requests.get(url, headers=_headers(), params=params, timeout=10)
    if resp.status_code != 200:
        return []

    result = []
    for pr in resp.json():
        creator = (pr.get("user") or {}).get("login", "")
        if creator.lower() != username.lower():
            continue
        created = pr.get("created_at", "")
        try:
            ct = datetime.datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(timezone.utc)
            if ct < since_utc or ct > until_utc:
                continue
        except Exception:
            pass

        result.append({
            "number": pr.get("number"),
            "title":  pr.get("title", ""),
            "state":  pr.get("state", ""),
            "url":    pr.get("html_url", ""),
            "created": _fmt_time(created)
        })

    return result


# ── 未完成 meeting-action ─────────────────────────────────

def _fetch_pending_actions(repo_full_name: str, username: str) -> list:
    """拉取 label=meeting-action, state=open, assignee=username 的 issues。"""
    owner, repo = repo_full_name.split("/", 1)
    url = f"{GITEA_BASE_URL}/api/v1/repos/{owner}/{repo}/issues"
    params = {
        "type":        "issues",
        "state":       "open",
        "labels":      "meeting-action",
        "assigned_by": username,
        "limit":       50,
        "page":        1
    }
    resp = requests.get(url, headers=_headers(), params=params, timeout=10)
    if resp.status_code != 200:
        return []

    result = []
    for issue in resp.json():
        due = ""
        # Gitea 自定义字段或 issue body 里的截止日期，尽力提取
        body = issue.get("body", "")
        import re
        m = re.search(r"截止[：:]\s*(\d{4}-\d{2}-\d{2})", body)
        if m:
            due = m.group(1)

        result.append({
            "number": issue.get("number"),
            "title":  issue.get("title", ""),
            "url":    issue.get("html_url", ""),
            "due":    due
        })

    return result


# ── 汇总入口 ──────────────────────────────────────────────

def fetch_member_activities(repo_full_name: str, username: str,
                            since: datetime.datetime, until: datetime.datetime) -> dict:
    """
    拉取单个成员的全部活动数据，返回结构：
    {
      "commits":         [...],   # 同 gitea-routine-report 格式
      "issues":          [...],
      "prs":             [...],
      "pending_actions": [...]    # label=meeting-action 未完成任务
    }
    """
    commits  = _fetch_commits(repo_full_name, username, since, until)
    issues   = _fetch_issues(repo_full_name, username, since, until)
    prs      = _fetch_prs(repo_full_name, username, since, until)
    pending  = _fetch_pending_actions(repo_full_name, username)

    # 复用 generate_report.py 的 build_summary 统计逻辑，计算文件类型分布
    file_type_summary = {"code": 0, "doc": 0, "data": 0, "image": 0, "other": 0}
    code_exts  = {".py", ".js", ".ts", ".cpp", ".c", ".h", ".java", ".go", ".m"}
    doc_exts   = {".md", ".txt", ".pdf", ".docx", ".doc", ".rst"}
    data_exts  = {".json", ".yaml", ".yml", ".csv", ".xml"}
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".svg"}
    import os as _os
    for c in commits:
        for f in c.get("files", []):
            ext = _os.path.splitext(f["filename"])[-1].lower()
            if ext in code_exts:       file_type_summary["code"] += 1
            elif ext in doc_exts:      file_type_summary["doc"] += 1
            elif ext in data_exts:     file_type_summary["data"] += 1
            elif ext in image_exts:    file_type_summary["image"] += 1
            else:                      file_type_summary["other"] += 1

    return {
        "username":         username,
        "commits":          commits,
        "commit_count":     len(commits),
        "total_additions":  sum(c["stats"]["additions"] for c in commits),
        "total_deletions":  sum(c["stats"]["deletions"] for c in commits),
        "file_type_summary": file_type_summary,
        "issues":           issues,
        "prs":              prs,
        "pending_actions":  pending
    }


def fetch_all_members_activities(repo_full_name: str, attendees: list[str],
                                 since: datetime.datetime,
                                 until: datetime.datetime) -> dict[str, dict]:
    """
    批量拉取所有参会成员的活动，返回 {username: activity_dict}。
    """
    result = {}
    for username in attendees:
        print(f"[activity_fetcher] 拉取 {username} 的活动数据...")
        try:
            result[username] = fetch_member_activities(repo_full_name, username, since, until)
        except Exception as e:
            print(f"[activity_fetcher] {username} 拉取失败（非致命）: {e}")
            result[username] = {
                "username": username, "commits": [], "commit_count": 0,
                "total_additions": 0, "total_deletions": 0,
                "file_type_summary": {},
                "issues": [], "prs": [], "pending_actions": []
            }
    return result