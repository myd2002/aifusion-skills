#!/usr/bin/env python3
"""
init_repo.py
科研项目仓库初始化脚本

功能：
  1. 幂等检查（README.md 是否存在）
  2. 创建仓库（可选）
  3. 写入标准目录结构和基础文档
  4. 创建初始 Issue
  5. 调用 imap-smtp-email Skill 发送邮件通知
  6. 任何步骤失败时发送失败邮件

用法：
  python3 init_repo.py \
    --repo-name <name> \
    --description <desc> \
    [--private true|false] \
    [--members user1,user2] \
    [--creator username] \
    [--existing]   # 若仓库已存在，跳过创建步骤
"""

import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from string import Template

import requests
from dotenv import load_dotenv

# ── 加载配置 ──────────────────────────────────────────────────
SKILL_DIR = Path(__file__).parent.parent
CONFIG_PATH = Path.home() / '.config' / 'project-init' / '.env'
FALLBACK_ENV = SKILL_DIR / '.env'

if CONFIG_PATH.exists():
    load_dotenv(CONFIG_PATH)
elif FALLBACK_ENV.exists():
    load_dotenv(FALLBACK_ENV)

GITEA_URL  = os.environ.get('GITEA_URL', 'http://43.156.243.152:3000').rstrip('/')
GITEA_TOKEN = os.environ.get('GITEA_TOKEN', '')
GITEA_ORG   = os.environ.get('GITEA_ORG', 'HKU-AIFusion')
EMAIL_SKILL_PATH = os.environ.get('EMAIL_SKILL_PATH', '')
EMAIL_ACCOUNT    = os.environ.get('EMAIL_ACCOUNT', '')

TEMPLATES_DIR = Path(__file__).parent / 'templates'

# ── Gitea API 工具函数 ─────────────────────────────────────────

def headers():
    return {
        'Authorization': f'token {GITEA_TOKEN}',
        'Content-Type': 'application/json',
    }


def api_get(path):
    return requests.get(f'{GITEA_URL}/api/v1{path}', headers=headers(), timeout=15)


def api_post(path, data):
    return requests.post(f'{GITEA_URL}/api/v1{path}', json=data, headers=headers(), timeout=15)


def api_put(path, data):
    return requests.put(f'{GITEA_URL}/api/v1{path}', json=data, headers=headers(), timeout=15)


# ── 邮件发送（调用 imap-smtp-email Skill）────────────────────

def send_email(to: str, subject: str, body: str) -> bool:
    """调用 imap-smtp-email Skill 的 smtp.js 发送邮件"""
    if not EMAIL_SKILL_PATH:
        print('[WARN] EMAIL_SKILL_PATH 未配置，跳过邮件发送', file=sys.stderr)
        return False

    smtp_script = Path(EMAIL_SKILL_PATH) / 'scripts' / 'smtp.js'
    if not smtp_script.exists():
        print(f'[WARN] 未找到 smtp.js: {smtp_script}', file=sys.stderr)
        return False

    cmd = ['node', str(smtp_script), 'send', '--to', to, '--subject', subject, '--body', body]
    if EMAIL_ACCOUNT:
        cmd = ['node', str(smtp_script), '--account', EMAIL_ACCOUNT, 'send',
               '--to', to, '--subject', subject, '--body', body]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f'[WARN] 邮件发送失败: {result.stderr}', file=sys.stderr)
    return result.returncode == 0


def get_user_email(username: str) -> str | None:
    """通过 Gitea API 获取用户邮箱"""
    resp = api_get(f'/users/{username}')
    if resp.status_code == 200:
        return resp.json().get('email')
    return None


def send_failure_email(creator: str, repo_name: str, failed_step: str, error_msg: str):
    """初始化失败时发送邮件通知创建者"""
    email = get_user_email(creator) if creator else None
    if not email:
        print(f'[WARN] 无法获取创建者邮箱，跳过失败通知', file=sys.stderr)
        return

    subject = f'[AIFusion] 仓库 {repo_name} 初始化失败'
    body = (
        f'仓库初始化过程中出现错误，请手动补全。\n\n'
        f'仓库名称：{repo_name}\n'
        f'失败步骤：{failed_step}\n'
        f'错误信息：{error_msg}\n\n'
        f'仓库地址：{GITEA_URL}/{GITEA_ORG}/{repo_name}\n\n'
        f'--- AIFusion Bot'
    )
    send_email(email, subject, body)


# ── 核心初始化函数 ─────────────────────────────────────────────

def check_idempotent(repo_name: str) -> bool:
    """检查 README.md 是否已存在（幂等保护）"""
    resp = api_get(f'/repos/{GITEA_ORG}/{repo_name}/contents/README.md')
    return resp.status_code == 200


def create_repo(repo_name: str, description: str, private: bool) -> bool:
    """创建 Gitea 仓库"""
    data = {
        'name': repo_name,
        'description': description,
        'private': private,
        'auto_init': False,
    }
    resp = api_post(f'/orgs/{GITEA_ORG}/repos', data)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f'创建仓库失败 HTTP {resp.status_code}: {resp.text}')
    return True


def write_file(repo_name: str, filepath: str, content: str, commit_msg: str) -> bool:
    """通过 Gitea API 写入文件"""
    content_b64 = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    data = {
        'message': commit_msg,
        'content': content_b64,
    }
    resp = api_post(f'/repos/{GITEA_ORG}/{repo_name}/contents/{filepath}', data)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f'写入 {filepath} 失败 HTTP {resp.status_code}: {resp.text}')
    return True


def create_issue(repo_name: str, title: str, body: str, assignees: list[str] | None = None) -> bool:
    """创建 Issue"""
    data = {
        'title': title,
        'body': body,
        'assignees': assignees or [],
    }
    resp = api_post(f'/repos/{GITEA_ORG}/{repo_name}/issues', data)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f'创建 Issue "{title}" 失败 HTTP {resp.status_code}: {resp.text}')
    return True


def render_template(tpl_name: str, variables: dict) -> str:
    """读取模板文件并替换变量"""
    tpl_path = TEMPLATES_DIR / tpl_name
    with open(tpl_path, 'r', encoding='utf-8') as f:
        tpl = Template(f.read())
    return tpl.safe_substitute(variables)


# ── 主流程 ────────────────────────────────────────────────────

def init_repo(repo_name: str, description: str, private: bool,
              members: list[str], creator: str, existing: bool) -> dict:
    """
    执行完整初始化流程，返回结果 dict。
    任何步骤抛出异常时，发送失败邮件并重新抛出。
    """
    repo_url = f'{GITEA_URL}/{GITEA_ORG}/{repo_name}'
    now = datetime.now().strftime('%Y-%m-%d')

    # Step 0: 幂等检查
    if not existing and check_idempotent(repo_name):
        return {
            'status': 'skipped',
            'message': f'仓库 {repo_name} 的 README.md 已存在，跳过初始化。',
            'repoUrl': repo_url,
        }

    # Step 1: 创建仓库（新建场景）
    if not existing:
        try:
            create_repo(repo_name, description, private)
        except Exception as e:
            send_failure_email(creator, repo_name, '创建仓库', str(e))
            raise

    # Step 2: 写入目录结构和文档
    gitkeep_content = '# 此文件用于保留空目录，请勿删除\n'
    gitkeep_msg = 'chore: 初始化标准目录结构'

    files_to_write = [
        ('meeting/.gitkeep',  gitkeep_content, gitkeep_msg),
        ('docs/.gitkeep',     gitkeep_content, gitkeep_msg),
        ('src/.gitkeep',      gitkeep_content, gitkeep_msg),
        ('data/.gitkeep',     gitkeep_content, gitkeep_msg),
        ('reports/.gitkeep',  gitkeep_content, gitkeep_msg),
    ]

    # 生成 README.md
    members_str = '\n'.join(f'- @{m}' for m in members) if members else '（待补充）'
    readme_vars = {
        'repo_name':   repo_name,
        'description': description,
        'created_by':  creator or '（未知）',
        'created_date': now,
        'members':     members_str,
        'gitea_url':   GITEA_URL,
        'org':         GITEA_ORG,
    }
    readme_content = render_template('README.md.tpl', readme_vars)
    files_to_write.append(('README.md', readme_content, 'docs: 添加 README.md'))

    # 固定 CONTRIBUTING.md
    contributing_content = render_template('CONTRIBUTING.md.tpl', {})
    files_to_write.append(('CONTRIBUTING.md', contributing_content, 'docs: 添加 CONTRIBUTING.md'))

    try:
        for filepath, content, msg in files_to_write:
            write_file(repo_name, filepath, content, msg)
    except Exception as e:
        send_failure_email(creator, repo_name, '写入目录结构和文档', str(e))
        raise

    # Step 3: 创建初始 Issue
    members_mention = ' '.join(f'@{m}' for m in members) if members else '（请创建者手动 @ 成员）'
    issue1_body = (
        f'仓库 `{repo_name}` 已完成自动初始化，欢迎所有成员查阅！\n\n'
        f'{members_mention}\n\n'
        f'**请务必阅读 [README.md]({repo_url}/src/branch/main/README.md) 了解：**\n'
        f'- 目录结构与各文件夹用途\n'
        f'- 分支命名规范\n'
        f'- Issue 使用说明\n'
        f'- 会议纪要上传规范\n\n'
        f'仓库地址：{repo_url}'
    )

    issue2_body = (
        f'请 @{creator or "创建者"} 在本 Issue 中明确各成员的工作职责，确认后关闭此 Issue。\n\n'
        f'建议格式：\n'
        f'| 成员 | 职责 |\n'
        f'|------|------|\n'
        f'| @xxx | 负责 xxx |\n\n'
        f'完成后请在此 Issue 留言确认，并关闭。'
    )

    try:
        create_issue(repo_name, '【全体通知】新项目仓库已创建，请查阅 README', issue1_body, assignees=[])
        if creator:
            create_issue(repo_name, '【启动任务】确认成员分工与职责', issue2_body, assignees=[creator])
        else:
            create_issue(repo_name, '【启动任务】确认成员分工与职责', issue2_body, assignees=[])
    except Exception as e:
        send_failure_email(creator, repo_name, '创建 Issue', str(e))
        raise

    # Step 4: 发送邮件通知成员
    email_errors = []
    if members:
        for username in members:
            email = get_user_email(username)
            if not email:
                email_errors.append(username)
                continue
            subject = f'[AIFusion] 你被加入了新项目仓库：{repo_name}'
            body = (
                f'Hi @{username}，\n\n'
                f'你被加入了新的科研项目仓库：{repo_name}\n\n'
                f'项目描述：{description}\n'
                f'创建者：{creator or "（未知）"}\n'
                f'仓库地址：{repo_url}\n\n'
                f'请查阅 README.md 了解项目结构和协作规范。\n\n'
                f'--- AIFusion Bot'
            )
            ok = send_email(email, subject, body)
            if not ok:
                email_errors.append(username)

    # 也通知创建者
    if creator:
        creator_email = get_user_email(creator)
        if creator_email:
            subject = f'[AIFusion] 仓库 {repo_name} 初始化成功'
            body = (
                f'Hi @{creator}，\n\n'
                f'仓库 {repo_name} 已成功初始化！\n\n'
                f'仓库地址：{repo_url}\n\n'
                f'已完成：\n'
                f'  ✅ 标准目录结构（meeting/ docs/ src/ data/ reports/）\n'
                f'  ✅ README.md 和 CONTRIBUTING.md\n'
                f'  ✅ 2 个启动期 Issue\n'
                + (f'  ⚠️  以下成员邮件发送失败，请手动通知：{", ".join(email_errors)}\n'
                   if email_errors else '  ✅ 成员邮件通知\n')
                + f'\n--- AIFusion Bot'
            )
            send_email(creator_email, subject, body)

    return {
        'status': 'success',
        'repoUrl': repo_url,
        'emailErrors': email_errors,
    }


# ── 命令行入口 ─────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description='初始化 Gitea 科研项目仓库')
    parser.add_argument('--repo-name',   required=True, help='仓库名称（英文）')
    parser.add_argument('--description', required=True, help='项目描述')
    parser.add_argument('--private',     default='false', help='是否私有（true/false）')
    parser.add_argument('--members',     default='',  help='成员用户名，逗号分隔')
    parser.add_argument('--creator',     default='',  help='创建者用户名')
    parser.add_argument('--existing',    action='store_true', help='仓库已存在，跳过创建步骤')
    return parser.parse_args()


def main():
    if not GITEA_TOKEN:
        print(json.dumps({'status': 'error', 'message': 'GITEA_TOKEN 未配置，请运行 setup.sh'}))
        sys.exit(1)

    args = parse_args()
    members = [m.strip() for m in args.members.split(',') if m.strip()] if args.members else []
    private = args.private.lower() == 'true'

    try:
        result = init_repo(
            repo_name=args.repo_name,
            description=args.description,
            private=private,
            members=members,
            creator=args.creator,
            existing=args.existing,
        )
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0)
    except Exception as e:
        err = {'status': 'error', 'message': str(e)}
        print(json.dumps(err, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
