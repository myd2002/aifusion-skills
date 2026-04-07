import os
import json
import argparse
from datetime import datetime, timezone
from dotenv import load_dotenv
from get_commits import get_all_repos, get_commits_by_repo
from get_admin_emails import get_admin_email

load_dotenv()


def build_summary(repo: str, commits: list, hours: int) -> dict:
    """把提交数据整理成结构化摘要，返回给 OpenClaw 分析"""
    time_desc = f"过去 {hours} 小时" if hours < 168 else "过去 7 天"

    # 按成员汇总
    member_stats = {}
    vague_commits = []

    for c in commits:
        author = c["author"]
        if author not in member_stats:
            member_stats[author] = {
                "commits": 0,
                "additions": 0,
                "deletions": 0,
                "messages": [],
                "vague_count": 0,
                "branches": set()
            }
        member_stats[author]["commits"] += 1
        member_stats[author]["additions"] += c["stats"]["additions"]
        member_stats[author]["deletions"] += c["stats"]["deletions"]
        member_stats[author]["messages"].append(c["message"])
        member_stats[author]["branches"].add(c["branch"])
        if c["is_vague"]:
            member_stats[author]["vague_count"] += 1
            vague_commits.append({
                "author": author,
                "message": c["message"],
                "time": c["time"]
            })

    # 把 set 转成 list 方便 JSON 序列化
    for author in member_stats:
        member_stats[author]["branches"] = list(
            member_stats[author]["branches"]
        )

    return {
        "repo": repo,
        "admin_email": get_admin_email(repo),
        "time_range": time_desc,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "has_commits": len(commits) > 0,
        "overview": {
            "total_commits": len(commits),
            "total_members": len(member_stats),
            "total_additions": sum(c["stats"]["additions"] for c in commits),
            "total_deletions": sum(c["stats"]["deletions"] for c in commits),
        },
        "members": member_stats,
        "vague_commits": vague_commits
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=str, default=None)
    parser.add_argument("--hours", type=int, default=168)
    args = parser.parse_args()

    repos = [args.repo] if args.repo else get_all_repos()

    results = []
    for repo in repos:
        commits = get_commits_by_repo(repo, args.hours)
        summary = build_summary(repo, commits, args.hours)
        results.append(summary)

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()