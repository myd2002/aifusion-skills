import requests
import os
import json
import argparse
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# 修复 import 路径问题
load_dotenv(os.path.expanduser("~/.config/gitea-routine-report/.env"))

GITEA_URL = os.getenv("GITEA_URL")
GITEA_TOKEN = os.getenv("GITEA_TOKEN")
HEADERS = {"Authorization": f"token {GITEA_TOKEN}"}

VAGUE_KEYWORDS = [
    "update", "fix", "modify", "change", "edit", "adjust",
    "修改", "更新", "修复", "调整", "改", "完善", "优化", "test", "测试"
]


def is_vague_message(message: str) -> bool:
    msg = message.strip().lower()
    if len(msg) < 10:
        return True
    for keyword in VAGUE_KEYWORDS:
        if msg in [keyword, keyword + ".", keyword + "s"]:
            return True
    return False


def get_all_repos() -> list:
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


def get_all_branch_commits(repo_full_name: str, since: datetime) -> dict:
    """
    对每个分支单独拉取 commit 列表，建立完整的 sha->branch 映射。
    这样每个 commit 都能准确对应到它所在的分支。
    """
    sha_to_branch = {}

    # 获取所有分支
    url = f"{GITEA_URL}/api/v1/repos/{repo_full_name}/branches"
    response = requests.get(url, headers=HEADERS, params={"limit": 50})
    if response.status_code != 200:
        return sha_to_branch

    branches = response.json()

    for branch in branches:
        branch_name = branch["name"]
        # 对每个分支单独拉取 commit 列表
        commits_url = f"{GITEA_URL}/api/v1/repos/{repo_full_name}/commits"
        params = {
            "sha": branch_name,
            "limit": 50,
            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        branch_resp = requests.get(commits_url, headers=HEADERS, params=params)
        if branch_resp.status_code == 200:
            for commit in branch_resp.json():
                sha = commit["sha"]
                # 只记录第一次出现（优先记录非 main 分支，更有信息量）
                if sha not in sha_to_branch:
                    sha_to_branch[sha] = branch_name
                elif branch_name != "main" and branch_name != "master":
                    sha_to_branch[sha] = branch_name

    return sha_to_branch


def get_commits_by_repo(repo_full_name: str, hours: int = 168) -> list:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    # 先建立完整的 sha->branch 映射
    sha_to_branch = get_all_branch_commits(repo_full_name, since)

    # 再拉取 commit 列表
    url = f"{GITEA_URL}/api/v1/repos/{repo_full_name}/commits"
    params = {
        "limit": 50,
        "page": 1,
        "since": since.strftime("%Y-%m-%dT%H:%M:%SZ")
    }
    response = requests.get(url, headers=HEADERS, params=params)
    if response.status_code != 200:
        return []

    commits_raw = response.json()
    result = []

    for commit in commits_raw:
        sha = commit["sha"]
        message = commit["commit"]["message"].strip()

        # 获取文件变动详情
        diff_url = f"{GITEA_URL}/api/v1/repos/{repo_full_name}/git/commits/{sha}"
        diff_resp = requests.get(diff_url, headers=HEADERS)
        files = []
        total_additions = 0
        total_deletions = 0
        if diff_resp.status_code == 200:
            for f in diff_resp.json().get("files", []):
                total_additions += f.get("additions", 0)
                total_deletions += f.get("deletions", 0)
                files.append({
                    "filename": f.get("filename", ""),
                    "status": f.get("status", ""),
                    "additions": f.get("additions", 0),
                    "deletions": f.get("deletions", 0)
                })

        # 从映射里取分支，取不到则用 main
        branch = sha_to_branch.get(sha, "main")

        result.append({
            "sha": sha[:8],
            "author": commit["commit"]["author"]["name"],
            "time": commit["commit"]["author"]["date"],
            "message": message,
            "is_vague": is_vague_message(message),
            "branch": branch,
            "stats": {
                "additions": total_additions,
                "deletions": total_deletions,
                "files_changed": len(files)
            },
            "files": files
        })

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=str, default=None)
    parser.add_argument("--hours", type=int, default=168)
    args = parser.parse_args()

    repos = [args.repo] if args.repo else get_all_repos()
    result = {}
    for repo in repos:
        result[repo] = get_commits_by_repo(repo, args.hours)
    print(json.dumps(result, ensure_ascii=False, indent=2))