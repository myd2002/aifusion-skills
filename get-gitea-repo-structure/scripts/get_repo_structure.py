import requests
import os
import argparse
import json
from dotenv import load_dotenv

# 读取 .env 配置
load_dotenv()
GITEA_URL = os.getenv("GITEA_URL")
GITEA_TOKEN = os.getenv("GITEA_TOKEN")

HEADERS = {"Authorization": f"token {GITEA_TOKEN}"}

# 文件分类规则
FILE_CATEGORIES = {
    "code": [".py", ".js", ".ts", ".cpp", ".c", ".h", ".java", ".go", ".rs", ".m", ".matlab"],
    "doc": [".md", ".txt", ".rst", ".docx", ".doc", ".pdf"],
    "slide": [".ppt", ".pptx"],
    "data": [".csv", ".json", ".yaml", ".yml", ".xml", ".xls", ".xlsx"],
    "image": [".png", ".jpg", ".jpeg", ".gif", ".svg", ".bmp"],
    "model": [".pt", ".pth", ".onnx", ".pkl", ".h5", ".bin"],
    "config": [".env", ".ini", ".cfg", ".toml", ".sh", ".bat"],
}


def classify_file(filename: str) -> str:
    """根据文件扩展名对文件进行分类"""
    ext = os.path.splitext(filename)[-1].lower()
    for category, extensions in FILE_CATEGORIES.items():
        if ext in extensions:
            return category
    return "other"


def get_default_branch(repo_full_name: str) -> str:
    """获取仓库的默认分支名"""
    url = f"{GITEA_URL}/api/v1/repos/{repo_full_name}"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return response.json().get("default_branch", "main")
    return "main"


def get_branch_sha(repo_full_name: str, branch: str) -> str:
    """获取指定分支最新 commit 的 SHA"""
    url = f"{GITEA_URL}/api/v1/repos/{repo_full_name}/branches/{branch}"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return response.json()["commit"]["id"]
    return None


def get_file_tree(repo_full_name: str, sha: str) -> list:
    """递归获取仓库完整文件树"""
    url = f"{GITEA_URL}/api/v1/repos/{repo_full_name}/git/trees/{sha}"
    params = {"recursive": True}
    response = requests.get(url, headers=HEADERS, params=params)
    if response.status_code != 200:
        return []
    return response.json().get("tree", [])


def build_structure(repo_full_name: str, branch: str = None) -> dict:
    """构建单个仓库的完整文件树快照"""
    # 确定分支
    if not branch:
        branch = get_default_branch(repo_full_name)

    # 获取分支最新 SHA
    sha = get_branch_sha(repo_full_name, branch)
    if not sha:
        return {"error": f"无法获取仓库 {repo_full_name} 的分支 {branch}"}

    # 获取文件树
    tree = get_file_tree(repo_full_name, sha)

    # 整理文件列表
    files = []
    category_summary = {}

    for item in tree:
        if item["type"] != "blob":  # 只处理文件，跳过目录
            continue

        path = item["path"]
        size = item.get("size", 0)
        category = classify_file(path)

        files.append({
            "path": path,
            "size_bytes": size,
            "category": category
        })

        # 统计各分类文件数量
        category_summary[category] = category_summary.get(category, 0) + 1

    # 提取顶层目录结构
    top_level = sorted(set(
        p["path"].split("/")[0] for p in files
    ))

    return {
        "repo": repo_full_name,
        "branch": branch,
        "commit_sha": sha[:8],
        "total_files": len(files),
        "category_summary": category_summary,
        "top_level_structure": top_level,
        "files": files
    }


def get_all_repos() -> list:
    """获取 Bot 能看到的所有仓库"""
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


def main():
    parser = argparse.ArgumentParser(description="获取 Gitea 仓库文件树快照")
    parser.add_argument("--repo", type=str, default=None,
                        help="指定仓库，格式：owner/reponame")
    parser.add_argument("--branch", type=str, default=None,
                        help="指定分支，默认为主分支")
    parser.add_argument("--all", action="store_true",
                        help="获取所有可见仓库的文件树")
    args = parser.parse_args()

    if args.all:
        repos = get_all_repos()
    elif args.repo:
        repos = [args.repo]
    else:
        # 默认获取所有仓库
        repos = get_all_repos()

    results = []
    for repo in repos:
        structure = build_structure(repo, args.branch)
        results.append(structure)

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()