"""
webhook_server.py
FastAPI webhook 端点，监听所有受管仓库的 Gitea push 事件。

判断逻辑：
  push 事件中若包含 confirmed_issue.md 的新增（added），
  且对应目录的 meta.yaml status == draft-pending-review，
  则调用 Skill-D 主流程。

安全：
  使用 HMAC-SHA256 验证 Gitea webhook secret，
  非法请求直接返回 403。

部署：
  服务器上以守护进程运行，监听 WEBHOOK_PORT（默认 8080）。
  Gitea 每个受管仓库需配置 webhook：
    URL:    http://43.156.243.152:{WEBHOOK_PORT}/webhook
    Secret: 与 WEBHOOK_SECRET 环境变量一致
    事件:   Push Events
"""

import os
import hmac
import hashlib
import json
import asyncio
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
app = FastAPI()


def _verify_signature(body: bytes, signature_header: str) -> bool:
    """
    验证 Gitea 发来的 X-Gitea-Signature 签名。
    Gitea 使用 HMAC-SHA256，格式为裸 hex 字符串（无 sha256= 前缀）。
    """
    if not WEBHOOK_SECRET:
        # 未配置 secret 时跳过验证（仅用于本地开发）
        return True
    if not signature_header:
        return False
    expected = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _extract_confirmed_meeting(payload: dict) -> tuple[str | None, str | None]:
    """
    从 push payload 中检测是否有 confirmed_issue.md 新增。
    返回 (repo_full_name, meeting_dir) 或 (None, None)。

    Gitea push payload 结构：
    {
      "repository": {"full_name": "owner/repo"},
      "commits": [
        {
          "added":    ["meetings/2026-04-22-1500/confirmed_issue.md"],
          "modified": [...],
          "removed":  [...]
        }
      ]
    }
    """
    repo_full_name = payload.get("repository", {}).get("full_name", "")
    if not repo_full_name:
        return None, None

    commits = payload.get("commits", [])
    for commit in commits:
        added_files = commit.get("added", [])
        for filepath in added_files:
            # 检测路径格式：meetings/YYYY-MM-DD-HHMM/confirmed_issue.md
            parts = filepath.strip("/").split("/")
            if (len(parts) == 3
                    and parts[0] == "meetings"
                    and parts[2] == "confirmed_issue.md"):
                meeting_dir = parts[1]
                return repo_full_name, meeting_dir

    return None, None


def _run_skill_d(repo: str, meeting_dir: str):
    """在后台线程中执行 Skill-D 主流程，避免阻塞 webhook 响应。"""
    # 延迟导入避免循环依赖
    from scripts.issue_parser  import parse_confirmed_issue
    from scripts.issue_creator import create_issues_for_meeting
    from scripts.notify        import notify_all
    from scripts.gitea_ops     import read_file, update_meta_yaml
    from scripts.common        import write_log, now_beijing
    import yaml

    print(f"[webhook] 触发 Skill-D: {repo}/{meeting_dir}")

    # 读取 meta.yaml 并校验状态
    content, sha = read_file(repo, f"meetings/{meeting_dir}/meta.yaml")
    if not content:
        print(f"[webhook] meta.yaml 不存在，跳过")
        return

    try:
        meta = yaml.safe_load(content)
    except Exception as e:
        print(f"[webhook] meta.yaml 解析失败: {e}")
        return

    # 幂等校验：只处理 draft-pending-review 状态
    if meta.get("status") != "draft-pending-review":
        print(f"[webhook] status={meta.get('status')}，非 draft-pending-review，跳过（幂等）")
        return

    # 读取 confirmed_issue.md
    confirmed_content, _ = read_file(
        repo, f"meetings/{meeting_dir}/confirmed_issue.md"
    )
    if not confirmed_content:
        print(f"[webhook] confirmed_issue.md 不存在，跳过")
        return

    # 解析 confirmed_issue.md
    items = parse_confirmed_issue(confirmed_content)
    if items is None:
        # 解析失败，通知组织者
        _notify_parse_failure(repo, meeting_dir, meta)
        return

    # 执行建 issue / 发邮件
    _execute(repo, meeting_dir, meta, sha, items)


def _notify_parse_failure(repo: str, meeting_dir: str, meta: dict):
    from scripts.gitea_ops import get_user_email
    from scripts.email_sender import send_plain_email
    from scripts.common import ADVISOR_USERNAME

    organizer = meta.get("organizer", "")
    email = get_user_email(organizer) if organizer else None
    if not email and ADVISOR_USERNAME:
        email = get_user_email(ADVISOR_USERNAME)
    if not email:
        return

    topic = meta.get("topic", "团队会议")
    send_plain_email(
        [email],
        f"【错误】{topic} confirmed_issue.md 解析失败",
        f"您好，\n\n{topic}（{meeting_dir}）的 confirmed_issue.md 解析失败，"
        f"请检查文件格式后重新上传。\n\n"
        f"文件路径：meetings/{meeting_dir}/confirmed_issue.md\n\n"
        f"---\n此邮件由 AIFusionBot 自动发送。"
    )


def _execute(repo: str, meeting_dir: str, meta: dict,
             sha: str, items: list):
    """issue 创建与通知的核心逻辑，供 webhook 和 CLI 共用。"""
    from scripts.issue_creator import create_issues_for_meeting
    from scripts.notify        import notify_all
    from scripts.gitea_ops     import update_meta_yaml, read_file
    from scripts.common        import write_log

    meeting_category = meta.get("meeting_category", "single-project")
    is_cross = (meeting_category == "cross-project")

    created_issues = []
    failed_items   = []

    if not is_cross:
        # 单项目会议：批量创建 Gitea issue
        created_issues, failed_items = create_issues_for_meeting(
            repo, meeting_dir, meta, items
        )

    # 发送通知邮件
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
            "is_cross_project":  is_cross,
            "created_count":     len(created_issues),
            "failed_count":      len(failed_items)
        }
    )
    print(f"[Skill-D] ✅ 完成: {repo}/{meeting_dir} "
          f"created={len(created_issues)} failed={len(failed_items)}")


@app.post("/webhook")
async def gitea_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()

    # 签名验证
    sig = request.headers.get("X-Gitea-Signature", "")
    if not _verify_signature(body, sig):
        raise HTTPException(status_code=403, detail="Invalid signature")

    # 只处理 push 事件
    event_type = request.headers.get("X-Gitea-Event", "")
    if event_type != "push":
        return {"status": "ignored", "reason": f"event={event_type}"}

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    repo, meeting_dir = _extract_confirmed_meeting(payload)
    if not repo or not meeting_dir:
        return {"status": "ignored", "reason": "no confirmed_issue.md found"}

    # 后台异步执行，立即返回 200 给 Gitea
    background_tasks.add_task(_run_skill_d, repo, meeting_dir)
    return {"status": "accepted", "repo": repo, "meeting_dir": meeting_dir}


@app.get("/health")
async def health():
    return {"status": "ok"}