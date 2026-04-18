"""
main.py
Skill-D create_issues 双模式入口。

模式 A（webhook server）：
  python main.py --mode webhook
  启动 FastAPI HTTP 服务，监听 Gitea push webhook。
  Gitea 检测到 confirmed_issue.md 新增时自动触发。

模式 B（CLI / OpenClaw 对话调用）：
  python main.py --mode cli --repo owner/repo --meeting-dir YYYY-MM-DD-HHMM
  组织者在 OpenClaw 中说"确认 YYYY-MM-DD-HHMM 的 issue"时调用。
"""

import sys
import os
import argparse
sys.path.insert(0, os.path.dirname(__file__))

from scripts.common import write_log, now_beijing


def run_cli(repo: str, meeting_dir: str) -> dict:
    """
    CLI 模式主流程，供 OpenClaw 对话触发调用。
    返回结果 dict，由 OpenClaw 转为友好消息展示给用户。
    """
    import yaml
    from scripts.gitea_ops    import read_file, update_meta_yaml
    from scripts.issue_parser  import parse_confirmed_issue
    from scripts.issue_creator import create_issues_for_meeting
    from scripts.notify        import notify_all
    from scripts.webhook_server import _notify_parse_failure, _execute

    print(f"[Skill-D CLI] 处理: {repo}/{meeting_dir}")

    # 读取 meta.yaml 并校验
    content, sha = read_file(repo, f"meetings/{meeting_dir}/meta.yaml")
    if not content:
        return {
            "success": False,
            "message": f"找不到 {repo}/meetings/{meeting_dir}/meta.yaml，请确认会议目录是否正确。"
        }

    try:
        meta = yaml.safe_load(content)
    except Exception as e:
        return {"success": False, "message": f"meta.yaml 解析失败：{e}"}

    # 幂等校验
    status = meta.get("status", "")
    if status == "minutes-published":
        return {
            "success": True,
            "message": f"{meeting_dir} 的 issue 已创建完毕（minutes-published），无需重复操作。"
        }
    if status != "draft-pending-review":
        return {
            "success": False,
            "message": (
                f"{meeting_dir} 当前状态为 {status}，"
                f"只有 draft-pending-review 状态的会议才能确认 issue。"
            )
        }

    # 查找 confirmed_issue.md（CLI 模式下允许文件名仍为 draft_issue.md）
    confirmed_content, _ = read_file(
        repo, f"meetings/{meeting_dir}/confirmed_issue.md"
    )
    if not confirmed_content:
        # 尝试读取 draft_issue.md（用户通过对话确认，未手动改名）
        confirmed_content, _ = read_file(
            repo, f"meetings/{meeting_dir}/draft_issue.md"
        )
    if not confirmed_content:
        return {
            "success": False,
            "message": (
                f"找不到 confirmed_issue.md 或 draft_issue.md，"
                f"请先在 Gitea 中将草稿文件改名为 confirmed_issue.md，或直接上传已审核版本。"
            )
        }

    # 解析
    items = parse_confirmed_issue(confirmed_content)
    if items is None:
        _notify_parse_failure(repo, meeting_dir, meta)
        return {
            "success": False,
            "message": "confirmed_issue.md 格式解析失败，已邮件通知组织者，请检查文件格式。"
        }

    # 执行
    meeting_category = meta.get("meeting_category", "single-project")
    is_cross = (meeting_category == "cross-project")

    created_issues = []
    failed_items   = []

    if not is_cross:
        from scripts.issue_creator import create_issues_for_meeting
        created_issues, failed_items = create_issues_for_meeting(
            repo, meeting_dir, meta, items
        )

    from scripts.notify import notify_all
    notify_all(
        repo=repo,
        meeting_dir=meeting_dir,
        meta=meta,
        items=items,
        created_issues=created_issues,
        failed_items=failed_items,
        is_cross_project=is_cross
    )

    # 更新 meta.yaml
    _, current_sha = read_file(repo, f"meetings/{meeting_dir}/meta.yaml")
    updates = {"status": "minutes-published"}
    if created_issues:
        updates["created_issues"] = [
            {"local_id": ci["local_id"], "issue_number": ci["issue_number"]}
            for ci in created_issues
        ]
    update_meta_yaml(repo, meeting_dir, updates, current_sha)

    write_log(
        "skill-d", repo, meeting_dir,
        "minutes-published", "ok",
        {
            "trigger":        "cli",
            "is_cross":       is_cross,
            "created_count":  len(created_issues),
            "failed_count":   len(failed_items)
        }
    )

    # 构造返回消息
    if is_cross:
        message = (
            f"✅ 跨项目会议 {meeting_dir} 已处理完毕。\n"
            f"已向组织者发送 issue 建议邮件（跨项目会议不自动建 issue）。"
        )
    else:
        failed_hint = ""
        if failed_items:
            failed_hint = (
                f"\n⚠️ {len(failed_items)} 条 issue 创建失败，"
                f"已在通知邮件中列出，请手动处理。"
            )
        message = (
            f"✅ {meeting_dir} 处理完成。\n"
            f"共创建 {len(created_issues)} 条 issue，"
            f"已发送全员通知和个人任务邮件。{failed_hint}"
        )

    return {"success": True, "message": message}


def run_webhook():
    """启动 Webhook HTTP 服务。"""
    import uvicorn
    from scripts.webhook_server import app

    port = int(os.environ.get("WEBHOOK_PORT", "8080"))
    print(f"[Skill-D] 启动 Webhook 服务，监听端口 {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Skill-D: create_issues")
    parser.add_argument(
        "--mode",
        choices=["webhook", "cli"],
        default="webhook",
        help="运行模式：webhook=HTTP服务，cli=单次执行"
    )
    parser.add_argument("--repo",        default=None, help="仓库 owner/repo（cli 模式必填）")
    parser.add_argument("--meeting-dir", default=None, help="会议目录名（cli 模式必填）")
    args = parser.parse_args()

    if args.mode == "webhook":
        run_webhook()

    elif args.mode == "cli":
        if not args.repo or not args.meeting_dir:
            print("错误：cli 模式需要 --repo 和 --meeting-dir 参数")
            sys.exit(1)
        result = run_cli(args.repo, args.meeting_dir)
        print(result["message"])
        sys.exit(0 if result["success"] else 1)