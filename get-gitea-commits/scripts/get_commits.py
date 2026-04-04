import requests
import os
import argparse
import json
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# 读取 .env 配置
load_dotenv()
GITEA_URL = os.getenv("GITEA_URL")
GITEA_TOKEN = os.getenv("GITEA_TOKEN")

HEADERS = {"Authorization": f"token {GITEA_TOKEN}"}


def get_all_repos():
    """获取 Bot 能看到的所有仓库（公共+已加入的私有）"""
    repos = []
    page = 1
    while True:
        url = f"{GITEA_URL}/api/v1/repos/search"
        params = {"limit": 50, "page": page, "token": GITEA_TOKEN}
        response = requests.get(url, headers=HEADERS, params=params)
        if response.status_code != 200:
            break
        data = response.json().get("data", [])
        if not data:
            break
        repos.extend(data)
        page += 1
    return [r["full_name"] for r in repos]


def get_commit_diff(repo_full_name, sha):
    """获取某次提交的具体diff内容"""
    url = f"{GITEA_URL}/api/v1/repos/{repo_full_name}/git/commits/{sha}"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return response.json()
    return {}


def get_commits_by_repo(repo_full_name, mode, limit=10, hours=24):
    """获取单个仓库的提交记录"""
    url = f"{GITEA_URL}/api/v1/repos/{repo_full_name}/commits"
    params = {"limit": limit, "page": 1}

    # 如果是时间段模式，计算起始时间
    if mode == "timerange":
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        params["since"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        params["limit"] = 50  # 时间段模式多拉一些

    response = requests.get(url, headers=HEADERS, params=params)
    if response.status_code != 200:
        return []

    commits_raw = response.json()
    result = []

    for commit in commits_raw:
        sha = commit["sha"]

        # 获取改动文件和diff
        diff_data = get_commit_diff(repo_full_name, sha)
        files = diff_data.get("files", [])
        file_details = []
        for f in files:
            file_details.append({
                "filename": f.get("filename", ""),
                "status": f.get("status", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "patch": f.get("patch", "")  # 具体diff内容
            })

        result.append({
            "repo": repo_full_name,
            "sha": sha[:8],  # 只取前8位，够用了
            "author": commit["commit"]["author"]["name"],
            "time": commit["commit"]["author"]["date"],
            "message": commit["commit"]["message"].strip(),
            "files": file_details
        })

    return result


def main():
    parser = argparse.ArgumentParser(description="获取 Gitea 提交记录")
    parser.add_argument("--mode", choices=["recent", "timerange"], default="recent",
                        help="查询模式：recent（按条数）或 timerange（按时间段）")
    parser.add_argument("--limit", type=int, default=10,
                        help="获取条数（mode=recent时使用）")
    parser.add_argument("--hours", type=int, default=24,
                        help="获取多少小时内的提交（mode=timerange时使用）")
    parser.add_argument("--repo", type=str, default=None,
                        help="指定仓库，格式：owner/reponame，不填则获取所有可见仓库")
    args = parser.parse_args()

    # 确定要查询的仓库列表
    if args.repo:
        repos = [args.repo]
    else:
        repos = get_all_repos()

    # 获取所有提交
    all_commits = []
    for repo in repos:
        commits = get_commits_by_repo(repo, args.mode, args.limit, args.hours)
        all_commits.extend(commits)

    # 输出结果（JSON格式，方便Agent读取）
    print(json.dumps(all_commits, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()