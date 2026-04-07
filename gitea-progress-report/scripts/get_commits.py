import requests
import os
import json
import argparse
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()
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


def get_branch_commit_map(repo_full_name: str) -> dict:
    """
    获取仓库所有分支及其最新 commit SHA 的映射。
    用于判断某个 commit 属于哪个分支。
    返回：{commit_sha: branch_name}
    """
    url = f"{GITEA_URL}/api/v1/repos/{repo_full_name}/branches"
    response = requests.get(url, headers=HEADERS, params={"limit": 50})
    sha_to_branch = {}
    if response.status_code == 200:
        for branch in response.json():
            sha = branch["commit"]["id"]
            name = branch["name"]
            sha_to_branch[sha] = name
    return sha_to_branch


def get_commits_by_repo(repo_full_name: str, hours: int = 168) -> list:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
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

    # 获取分支映射
    sha_to_branch = get_branch_commit_map(repo_full_name)

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

        # 判断分支：用 SHA 匹配，匹配不到则标记为 main
        branch = sha_to_branch.get(sha, None)
        if not branch:
            # 取第一个分支作为默认（大多数情况是 main）
            branch = list(sha_to_branch.values())[0] if sha_to_branch else "main"

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