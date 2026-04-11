#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
import sys
import traceback

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(CURRENT_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from common import (
    SkillError,
    parse_attendees,
    make_meeting_dir_name,
    build_meta_yaml_text,
    build_agenda_markdown,
    load_settings,
    safe_json_dumps,
)
from parse_intent import parse_user_intent
from tencent_meeting import TencentMeetingClient
from gitea_ops import GiteaOps
from email_sender import EmailSender


def parse_args():
    parser = argparse.ArgumentParser(description="Skill-A create meeting")
    parser.add_argument("--query", required=True, help="用户自然语言原始指令")
    parser.add_argument("--organizer", required=True, help="组织者 Gitea 用户名")
    parser.add_argument("--attendees", default="", help="逗号分隔的与会者 Gitea 用户名")
    parser.add_argument("--repo_hint", default="", help="可选仓库提示")
    parser.add_argument("--topic_hint", default="", help="可选主题提示")
    parser.add_argument("--dry_run", action="store_true", help="仅解析，不实际创建会议和文件")
    return parser.parse_args()


def print_json(payload):
    print(safe_json_dumps(payload, indent=2, ensure_ascii=False))


def print_success(**kwargs):
    payload = {"ok": True}
    payload.update(kwargs)
    print_json(payload)


def print_error(status, message, stage="", **kwargs):
    payload = {
        "ok": False,
        "status": status,
        "message": message,
    }
    if stage:
        payload["stage"] = stage
    payload.update(kwargs)
    print_json(payload)


def rollback_meeting_if_needed(meeting_client, meeting_created, created_meeting_info):
    if not meeting_created or not created_meeting_info:
        return
    try:
        meeting_client.cancel_meeting(created_meeting_info["meeting_id"])
    except Exception:
        pass


def append_error_log_if_possible(gitea, query, error_text, tb_text=""):
    try:
        gitea.append_log(
            {
                "skill": "skill-a",
                "repo": "",
                "meeting_dir": "",
                "action": "meeting-created",
                "status": "error",
                "details": {
                    "query": query,
                    "error": error_text,
                    "traceback": tb_text,
                },
            }
        )
    except Exception:
        pass


def main():
    args = parse_args()
    settings = load_settings()

    gitea = GiteaOps(settings)
    mailer = EmailSender(settings)
    meeting_client = TencentMeetingClient(settings)

    meeting_created = False
    created_meeting_info = None

    try:
        attendees = parse_attendees(args.attendees)

        intent = parse_user_intent(
            query=args.query,
            organizer=args.organizer,
            attendees=attendees,
            repo_hint=args.repo_hint.strip(),
            topic_hint=args.topic_hint.strip(),
            settings=settings,
        )

        repo = intent.get("repo")
        if not repo:
            repo_candidates = gitea.get_managed_repos()
            cross_repo = settings["AIFUSION_META_REPO"]
            if cross_repo and cross_repo not in repo_candidates:
                repo_candidates.append(cross_repo)

            print_error(
                status="need_repo",
                message="缺少项目归属，无法继续创建会议。",
                stage="resolve_repo",
                question="这是哪个项目的会议，还是跨项目会议？",
                repo_candidates=repo_candidates,
                parsed_intent={
                    "topic": intent.get("topic"),
                    "scheduled_time": str(intent.get("scheduled_time")),
                    "duration_minutes": intent.get("duration_minutes"),
                    "type": intent.get("type"),
                    "series_id": intent.get("series_id"),
                    "recurrence": intent.get("recurrence"),
                    "attendees": intent.get("attendees"),
                    "source": intent.get("source"),
                },
            )
            return

        scheduled_time = intent["scheduled_time"]
        duration_minutes = int(
            intent.get("duration_minutes", settings["DEFAULT_MEETING_DURATION_MINUTES"])
        )
        topic = intent.get("topic") or "未命名会议"
        meeting_type = intent.get("type", "ad-hoc")
        series_id = intent.get("series_id")
        recurrence = intent.get("recurrence")

        attendees_final = intent.get("attendees") or attendees or [args.organizer]
        if args.organizer not in attendees_final:
            attendees_final.insert(0, args.organizer)

        meeting_category = (
            "cross-project"
            if repo == settings["AIFUSION_META_REPO"]
            else "single"
        )
        meeting_dir = make_meeting_dir_name(scheduled_time)

        if args.dry_run:
            print_success(
                status="dry_run",
                preview={
                    "repo": repo,
                    "meeting_dir": meeting_dir,
                    "topic": topic,
                    "scheduled_time": scheduled_time.isoformat(),
                    "duration_minutes": duration_minutes,
                    "type": meeting_type,
                    "series_id": series_id,
                    "recurrence": recurrence,
                    "meeting_category": meeting_category,
                    "attendees": attendees_final,
                },
            )
            return

        # 1. 创建腾讯会议
        created_meeting_info = meeting_client.create_meeting(
            topic=topic,
            scheduled_time=scheduled_time,
            duration_minutes=duration_minutes,
            organizer=args.organizer,
            attendees=attendees_final,
            recurrence=recurrence,
        )
        meeting_created = True

        # 2. 查找上次会议摘要，用于 agenda.md
        previous_summary = gitea.get_previous_meeting_summary(
            repo=repo,
            current_scheduled_time=scheduled_time,
            meeting_type=meeting_type,
            series_id=series_id,
            meeting_category=meeting_category,
        )

        # 3. 生成 meta.yaml / agenda.md 内容
        meta_yaml = build_meta_yaml_text(
            meeting_info=created_meeting_info,
            topic=topic,
            scheduled_time=scheduled_time,
            duration_minutes=duration_minutes,
            meeting_type=meeting_type,
            series_id=series_id,
            meeting_category=meeting_category,
            repo=repo,
            organizer=args.organizer,
            attendees=attendees_final,
        )

        agenda_md = build_agenda_markdown(
            topic=topic,
            scheduled_time=scheduled_time,
            organizer=args.organizer,
            attendees=attendees_final,
            repo=repo,
            join_url=created_meeting_info["join_url"],
            meeting_code=created_meeting_info["meeting_code"],
            previous_summary=previous_summary,
        )

        # 4. 写入 Gitea
        gitea.create_or_update_text_file(
            repo_full_name=repo,
            file_path=f"meetings/{meeting_dir}/meta.yaml",
            content=meta_yaml,
            commit_message=f"feat(meeting): initialize {meeting_dir} meta.yaml",
        )

        gitea.create_or_update_text_file(
            repo_full_name=repo,
            file_path=f"meetings/{meeting_dir}/agenda.md",
            content=agenda_md,
            commit_message=f"feat(meeting): initialize {meeting_dir} agenda.md",
        )

        # 5. 查询邮箱并发送邀请邮件
        recipient_info = gitea.get_user_emails(attendees_final)
        send_result = mailer.send_meeting_invitation(
            recipients=recipient_info,
            topic=topic,
            scheduled_time=scheduled_time,
            duration_minutes=duration_minutes,
            join_url=created_meeting_info["join_url"],
            meeting_code=created_meeting_info["meeting_code"],
            repo=repo,
            meeting_dir=meeting_dir,
            organizer=args.organizer,
            gitea_base_url=settings["GITEA_BASE_URL"],
        )

        # 6. 写日志
        gitea.append_log(
            {
                "skill": "skill-a",
                "repo": repo,
                "meeting_dir": meeting_dir,
                "action": "meeting-created",
                "status": "ok",
                "details": {
                    "topic": topic,
                    "meeting_id": created_meeting_info["meeting_id"],
                    "meeting_code": created_meeting_info["meeting_code"],
                    "success_recipients": send_result["success_count"],
                    "failed_recipients": send_result["failed"],
                },
            }
        )

        agenda_url = gitea.build_blob_url(
            repo, f"meetings/{meeting_dir}/agenda.md"
        )

        print_success(
            status="created",
            repo=repo,
            meeting_dir=meeting_dir,
            meeting_id=created_meeting_info["meeting_id"],
            meeting_code=created_meeting_info["meeting_code"],
            join_url=created_meeting_info["join_url"],
            agenda_url=agenda_url,
            message=f"会议已建立，已完成仓库初始化与邮件通知（成功 {send_result['success_count']} 人）。",
        )

    except SkillError as e:
        rollback_meeting_if_needed(meeting_client, meeting_created, created_meeting_info)
        append_error_log_if_possible(gitea, args.query, str(e))
        print_error(
            status="error",
            stage=e.stage,
            message=str(e),
        )

    except Exception as e:
        rollback_meeting_if_needed(meeting_client, meeting_created, created_meeting_info)
        tb_text = traceback.format_exc()
        append_error_log_if_possible(gitea, args.query, str(e), tb_text)
        print_error(
            status="error",
            stage="unexpected",
            message=f"未预期异常：{e}",
        )


if __name__ == "__main__":
    main()