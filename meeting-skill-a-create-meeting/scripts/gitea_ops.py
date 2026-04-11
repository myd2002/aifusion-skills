# -*- coding: utf-8 -*-

import base64
import json
from datetime import timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from common import (
    SkillError,
    now_local,
    safe_json_dumps,
)


class GiteaOps:
    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings
        self.base_url = settings["GITEA_BASE_URL"].rstrip("/")
        self.token = settings["GITEA_TOKEN_BOT"]
        self.default_branch = settings["GITEA_DEFAULT_BRANCH"]
        self.meta_repo = settings["AIFUSION_META_REPO"]

        if not self.base_url:
            raise SkillError("缺少 GITEA_BASE_URL", stage="config")
        if not self.token:
            raise SkillError("缺少 GITEA_TOKEN_BOT", stage="config")
        if not self.meta_repo:
            raise SkillError("缺少 AIFUSION_META_REPO", stage="config")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"token {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _api(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}/api/v1{path}"
        resp = requests.request(method, url, headers=self._headers(), timeout=60, **kwargs)
        if resp.status_code >= 400:
            raise SkillError(
                f"Gitea API 失败：{method} {path} => {resp.status_code} {resp.text[:500]}",
                stage="gitea_api",
            )
        if resp.text.strip():
            return resp.json()
        return None

    @staticmethod
    def _split_repo(repo_full_name: str):
        if "/" not in repo_full_name:
            raise SkillError(f"非法 repo_full_name：{repo_full_name}", stage="repo_name")
        owner, repo = repo_full_name.split("/", 1)
        return owner, repo

    def get_managed_repos(self) -> List[str]:
        repos = []
        page = 1
        while True:
            data = self._api("GET", f"/repos/search?limit=50&page={page}")
            batch = data.get("data", [])
            if not batch:
                break

            for repo in batch:
                full_name = repo.get("full_name")
                if not full_name:
                    continue
                try:
                    if self.has_meetings_dir(full_name):
                        repos.append(full_name)
                except Exception:
                    continue
            page += 1
        return repos

    def has_meetings_dir(self, repo_full_name: str) -> bool:
        owner, repo = self._split_repo(repo_full_name)
        path = quote("meetings", safe="")
        url = f"/repos/{owner}/{repo}/contents/{path}?ref={self.default_branch}"
        try:
            self._api("GET", url)
            return True
        except SkillError:
            return False

    def get_user_emails(self, usernames: List[str]) -> List[Dict[str, str]]:
        result = []
        for username in usernames:
            try:
                data = self._api("GET", f"/users/{username}")
                email = data.get("email", "") or ""
                if email:
                    result.append({"username": username, "email": email})
            except Exception:
                continue
        return result

    def get_file(self, repo_full_name: str, file_path: str) -> Optional[Dict[str, Any]]:
        owner, repo = self._split_repo(repo_full_name)
        path = quote(file_path, safe="")
        url = f"/repos/{owner}/{repo}/contents/{path}?ref={self.default_branch}"
        try:
            return self._api("GET", url)
        except SkillError:
            return None

    def get_file_text(self, repo_full_name: str, file_path: str) -> Optional[str]:
        info = self.get_file(repo_full_name, file_path)
        if not info:
            return None
        content = info.get("content")
        encoding = info.get("encoding")
        if encoding == "base64" and content:
            return base64.b64decode(content).decode("utf-8")
        return None

    def create_or_update_text_file(
        self,
        repo_full_name: str,
        file_path: str,
        content: str,
        commit_message: str,
    ):
        owner, repo = self._split_repo(repo_full_name)
        existing = self.get_file(repo_full_name, file_path)
        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        api_path = f"/repos/{owner}/{repo}/contents/{quote(file_path, safe='')}"
        payload = {
            "branch": self.default_branch,
            "content": encoded,
            "message": commit_message,
        }
        if existing and existing.get("sha"):
            payload["sha"] = existing["sha"]
            self._api("PUT", api_path, json=payload)
        else:
            self._api("POST", api_path, json=payload)

    def append_log(self, record: Dict[str, Any]):
        now = now_local(self.settings["TZ"])
        date_str = now.strftime("%Y-%m-%d")
        file_path = f"logs/{date_str}.jsonl"

        record = dict(record)
        record["ts"] = now.isoformat()

        old_text = self.get_file_text(self.meta_repo, file_path) or ""
        new_text = old_text + safe_json_dumps(record, ensure_ascii=False) + "\n"

        self.create_or_update_text_file(
            repo_full_name=self.meta_repo,
            file_path=file_path,
            content=new_text,
            commit_message=f"chore(log): append {date_str}.jsonl",
        )

    def build_blob_url(self, repo_full_name: str, file_path: str) -> str:
        owner, repo = self._split_repo(repo_full_name)
        return f"{self.base_url}/{owner}/{repo}/src/branch/{self.default_branch}/{file_path}"

    def list_meeting_dirs(self, repo_full_name: str) -> List[str]:
        owner, repo = self._split_repo(repo_full_name)
        try:
            data = self._api(
                "GET",
                f"/repos/{owner}/{repo}/contents/{quote('meetings', safe='')}?ref={self.default_branch}",
            )
        except SkillError:
            return []

        result = []
        if isinstance(data, list):
            for item in data:
                if item.get("type") == "dir":
                    result.append(item["name"])
        return result

    def get_previous_meeting_summary(
        self,
        repo: str,
        current_scheduled_time,
        meeting_type: str,
        series_id: Optional[str],
        meeting_category: str,
    ) -> Dict[str, Any]:
        """
        简化版：
        1. 找 meetings/ 下所有目录
        2. 读取各目录 meta.yaml / minutes.md
        3. 选择当前时间之前最近的一次
        4. 直接从 minutes.md 提取前 5 条非空行作为摘要
        """
        dirs = self.list_meeting_dirs(repo)
        candidates = []

        for d in dirs:
            meta_text = self.get_file_text(repo, f"meetings/{d}/meta.yaml")
            minutes_text = self.get_file_text(repo, f"meetings/{d}/minutes.md")
            if not meta_text:
                continue

            try:
                import yaml
                meta = yaml.safe_load(meta_text) or {}
            except Exception:
                continue

            scheduled_time_raw = meta.get("scheduled_time")
            if not scheduled_time_raw:
                continue

            from dateutil import parser as dt_parser
            dt = dt_parser.parse(scheduled_time_raw)

            if dt >= current_scheduled_time:
                continue

            if meeting_type == "recurring" and series_id and meta.get("series_id") != series_id:
                continue

            if meta.get("meeting_category") != meeting_category:
                continue

            candidates.append(
                {
                    "dir": d,
                    "scheduled_time": dt,
                    "minutes_text": minutes_text or "",
                }
            )

        if not candidates:
            return {
                "source": "暂无参考记录",
                "summary_bullets": [],
            }

        candidates.sort(key=lambda x: x["scheduled_time"], reverse=True)
        best = candidates[0]

        bullets = []
        for line in best["minutes_text"].splitlines():
            text = line.strip().lstrip("-").strip()
            if text:
                bullets.append(text)
            if len(bullets) >= 5:
                break

        return {
            "source": f"{best['dir']} / minutes.md",
            "summary_bullets": bullets,
        }