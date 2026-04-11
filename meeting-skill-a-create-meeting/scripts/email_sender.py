# -*- coding: utf-8 -*-

import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List

from common import SkillError


class EmailSender:
    def __init__(self, settings: Dict):
        self.settings = settings
        self.host = settings["SMTP_HOST"]
        self.port = settings["SMTP_PORT"]
        self.use_ssl = settings["SMTP_USE_SSL"]
        self.user = settings["SMTP_USER"]
        self.password = settings["SMTP_PASSWORD"]
        self.sender_name = settings["SMTP_SENDER_NAME"]

        if not self.host:
            raise SkillError("缺少 SMTP_HOST", stage="config")
        if not self.user:
            raise SkillError("缺少 SMTP_USER", stage="config")
        if not self.password:
            raise SkillError("缺少 SMTP_PASSWORD", stage="config")

    def _build_message(self, to_email: str, subject: str, html_body: str) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{self.sender_name} <{self.user}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        return msg

    def _send_one(self, to_email: str, subject: str, html_body: str):
        msg = self._build_message(to_email, subject, html_body)

        if self.use_ssl:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=30) as server:
                server.login(self.user, self.password)
                server.sendmail(self.user, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                server.starttls()
                server.login(self.user, self.password)
                server.sendmail(self.user, [to_email], msg.as_string())

    def send_meeting_invitation(
        self,
        recipients: List[Dict[str, str]],
        topic: str,
        scheduled_time: datetime,
        duration_minutes: int,
        join_url: str,
        meeting_code: str,
        repo: str,
        meeting_dir: str,
        organizer: str,
        gitea_base_url: str,
    ) -> Dict:
        subject = f"【会议邀请】{topic}（{scheduled_time.strftime('%Y-%m-%d %H:%M')}）"
        agenda_url = f"{gitea_base_url.rstrip('/')}/{repo}/src/branch/main/meetings/{meeting_dir}/agenda.md"

        success = 0
        failed = []

        for item in recipients:
            username = item["username"]
            email = item["email"]

            html_body = f"""
            <html>
              <body style="font-family: Arial, sans-serif; line-height: 1.6;">
                <h2>会议邀请</h2>
                <p><b>主题：</b>{topic}</p>
                <p><b>时间：</b>{scheduled_time.strftime('%Y-%m-%d %H:%M %Z')}</p>
                <p><b>时长：</b>{duration_minutes} 分钟</p>
                <p><b>组织者：</b>{organizer}</p>
                <p><b>所属项目：</b>{repo}</p>
                <p><b>会议号：</b>{meeting_code}</p>
                <p><b>入会链接：</b><a href="{join_url}">{join_url}</a></p>
                <p><b>Agenda：</b><a href="{agenda_url}">{agenda_url}</a></p>
                <hr/>
                <p>Hi {username}，</p>
                <p>本次会议已创建，请按时参加。如需补充议程，请直接编辑仓库中的 agenda.md。</p>
                <p>AIFusionBot</p>
              </body>
            </html>
            """
            try:
                self._send_one(email, subject, html_body)
                success += 1
            except Exception as e:
                failed.append({"username": username, "email": email, "error": str(e)})

        return {
            "success_count": success,
            "failed": failed,
        }