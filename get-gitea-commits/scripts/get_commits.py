import requests
import os
import argparse
import json
import re
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# 读取 .env 配置
load_dotenv()
GITEA_URL = os.getenv("GITEA_URL")
GITEA_TOKEN = os.getenv("GITEA_TOKEN")

HEADERS = {"Authorization": f"token {GITEA_TOKEN}"}

# 模糊 commit message 的判断关键词
VAGUE_KEYWORDS = [
    "update", "fix", "modify", "change", "edit", "adjust",
    "修改", "更新", "修复", "调整", "改", "完善", "优化", "test", "测试"
]


def is_vague_message(message: str) -> bool:
    """判断 commit message 是否模糊"""
    msg = message.strip().lower()
    # 如果整条消息就是一个模糊词，或者非常短（少于10个字符）
    if len(msg) < 10:
        return True
    for keyword in VAGUE_KEYWORDS:
        if msg == keyword or msg == keyword + "." or msg == keyword + "s":
            return True
    return False


def extract_function_names(patch: str) -> list:
    """从 diff patch 里提取函数名（摘要化 Diff）"""
    functions = []
    # 匹配 Python 函数定义
    py_pattern = re.findall(r'^\+\s*def\s+(\w+)\s*\(', patch, re.MULTILINE)
    # 匹配 JavaScript/C++ 函数定义
    js_pattern = re.findall(r'^\+\s*(?:function\s+(\w+)|(\w+)\s*=\s*(?:async\s+)?(?:function|\())', patch, re.MULTILINE)
    functions.extend(py_pattern)
    for match in js_pattern:
        functions.extend([m for m in match if m])
    return list(set(functions))


def summarize_diff(patch: str, is_vague: bool) -> dict:
    """
    根据 commit message 是否模糊，决定返回多少 Diff 信息。
    - 清晰 message：只返回函数名 + 前10行改动
    - 模糊 message：返回完整 Diff
    """
    if not patch:
        return {"mode": "no_diff", "content": ""}

    if is_vague:
        # 模糊消息：返回完整 Diff
        return {
            "mode": "full_diff",
            "content": patch
        }
    else:
        # 清晰消息：只返回摘要
        lines = patch.split("\n")
        preview = "\n".join(lines[:10])  # 前10行
        functions = extract_function_names(patch)
        return {
            "mode": "summary_diff",
            "preview": preview,
            "changed_functions": functions
        }


def get_all_repos():
    """获取 Bot 能看到的所有仓库（公共+已加入的私有）"""
    repos = []
    page = 1
    while True:
        url = f"{GITEA_URL}/api/v1/repos/search"
        params = {"limit": 50, "page": page}
        response = requests.get(url, headers=HEADERS, params=params)
        if response.status_code != 200:
            break
        data = response.json().get("data", [])
        if not data:
            break
        repos.extend(data)
        page += 1
    return [r["full_name"] for r in repos]


def get_commit_diff(repo_full_name: str, sha: str) -> dict:
    """获取某次提交的具体 diff 内容"""
    url = f"{GITEA_URL}/api/v1/repos/{repo_full_name}/git/commits/{sha}"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return response.json()
    return {}


def get_commits_by_repo(repo_full_name: str, mode: str, limit: int = 10, hours: int = 24):
    """获取单个仓库的提交记录"""
    url = f"{GITEA_URL}/api/v1/repos/{repo_full_name}/commits"
    params = {"limit": limit, "page": 1}

    if mode == "timerange":
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        params["since"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        params["limit"] = 50

    response = requests.get(url, headers=HEADERS, params=params)
    if response.status_code != 200:
        return []

    commits_raw = response.json()
    result = []

    for commit in commits_raw:
        sha = commit["sha"]
        message = commit["commit"]["message"].strip()
        vague = is_vague_message(message)

        # 获取 diff 数据
        diff_data = get_commit_diff(repo_full_name, sha)
        files = diff_data.get("files", [])

        file_details = []
        for f in files:
            patch = f.get("patch", "")
            diff_summary = summarize_diff(patch, vague)

            file_details.append({
                "filename": f.get("filename", ""),
                "status": f.get("status", ""),          # added / modified / deleted
                "additions": f.get("additions", 0),      # 新增行数
                "deletions": f.get("deletions", 0),      # 删除行数
                "diff": diff_summary                      # 智能 diff
            })

        # 获取 parent commits（关联性）
        parents = [p["sha"][:8] for p in commit.get("parents", [])]

        result.append({
            "repo": repo_full_name,
            "sha": sha[:8],
            "branch": commit.get("ref", "unknown"),      # 分支名
            "author": commit["commit"]["author"]["name"],
            "time": commit["commit"]["author"]["date"],
            "message": message,
            "is_vague": vague,                           # 标记消息是否模糊
            "parent_shas": parents,                      # 父提交 SHA，用于关联分析
            "stats": {
                "total_additions": sum(f.get("additions", 0) for f in files),
                "total_deletions": sum(f.get("deletions", 0) for f in files),
                "files_changed": len(files)
            },
            "files": file_details
        })

    return result


def main():
    parser = argparse.ArgumentParser(description="获取 Gitea 提交记录")
    parser.add_argument("--mode", choices=["recent", "timerange"], default="recent")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--repo", type=str, default=None)
    args = parser.parse_args()

    if args.repo:
        repos = [args.repo]
    else:
        repos = get_all_repos()

    all_commits = []
    for repo in repos:
        commits = get_commits_by_repo(repo, args.mode, args.limit, args.hours)
        all_commits.extend(commits)

    print(json.dumps(all_commits, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()