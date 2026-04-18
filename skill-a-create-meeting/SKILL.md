---
name: skill-a-create-meeting
description: "当用户提出创建、预约、安排会议相关需求时触发此技能。OpenClaw 负责意图分析和参数补全；tencent-meeting-skill 负责会议创建；imap-smtp-email 负责邮件发送；本技能只负责 Gitea 会议目录、meta.yaml、agenda.md、仓库成员/邮箱查询、日志写入和邮件内容编排。"
author: mayidan
---

# Skill-A: create_meeting — 执行型会议创建技能

## 定位

本技能 **不负责 LLM 分析**，也 **不直接操作腾讯会议或 SMTP**。

职责分工：
- **OpenClaw**：理解用户自然语言、补问缺失参数、决定项目归属
- **tencent-meeting-skill**：创建腾讯会议，返回 `meeting_id / meeting_code / join_url`
- **imap-smtp-email**：实际发送邀请邮件
- **Skill-A**：在 Gitea 中创建会议目录与文件、查项目成员邮箱、生成邮件 HTML、写日志、把下一步需要发送的内容组织好返回给 OpenClaw

## 配置要求

配置文件路径：`~/.config/skill-a-create-meeting/.env`

首次使用前运行：
```bash
bash setup.sh
```

## 输入前提

OpenClaw 在调用本技能前，必须已经拿到以下信息：
- `time`：会议开始时间（ISO8601，带时区，默认 +08:00）
- `topic`：会议主题
- `repo`：目标仓库，如 `owner/repo`
- `category`：`single` 或 `cross-project`
- `organizer`：组织者 Gitea 用户名
- `meeting_id`：腾讯会议内部 ID
- `meeting_code`：9 位会议号
- `join_url`：加入链接

可选：
- `duration`：默认 60 分钟
- `attendees`：逗号分隔的 Gitea 用户名列表；若不传，则自动取该仓库成员
- `meeting_type`：`ad-hoc` 或 `recurring`
- `series_id`：循环会议系列 ID

## 严格工作流

### 第一步：由 OpenClaw 解析用户意图并补齐参数

不要在本技能内再次调用任何 LLM。

### 第二步：由 OpenClaw 调用 tencent-meeting-skill 创建腾讯会议

OpenClaw 应先调用腾讯会议 skill，拿到：
- `meeting_id`
- `meeting_code`
- `join_url`

### 第三步：调用本技能完成 Gitea 侧落库与邮件内容准备

```bash
node main.js prepare \
  --time "2026-04-22T15:00:00+08:00" \
  --topic "v2 设计评审" \
  --repo "mayidan/project-test" \
  --category "single" \
  --organizer "mayidan" \
  --meeting-id "xxx" \
  --meeting-code "123456789" \
  --join-url "https://meeting.tencent.com/..." \
  --duration 60
```

本技能会：
1. 解析时间并确定会议目录名 `meetings/YYYY-MM-DD-HHMM/`
2. 自动获取仓库成员（如果 `--attendees` 未传）
3. 获取成员邮箱与仓库 owner 邮箱
4. 查找上次同类会议并生成 agenda 回顾（无摘要能力时降级为“暂无参考记录”）
5. 创建 `meta.yaml` 和 `agenda.md`
6. 生成邀请邮件 HTML 与主题
7. 写运行日志到 `aifusion-meta/logs/YYYY-MM-DD.jsonl`
8. 把所有后续发信所需信息作为 JSON 返回

### 第四步：由 OpenClaw 调用 imap-smtp-email 发送邀请邮件

收到本技能输出后，OpenClaw 必须使用 `imap-smtp-email` 发送：
- 收件人：`invite_email.to`
- 主题：`invite_email.subject`
- HTML 正文：`invite_email.html`

## 输出 JSON 说明

本技能返回 JSON，关键字段包括：
- `user_message`：展示给用户的话术
- `invite_email`：邀请邮件发件参数
- `repo_member_usernames`：仓库成员列表
- `repo_member_emails`：仓库成员邮箱列表
- `repo_owner_email`：仓库 owner 邮箱（供后续 Skill-B 发会前简报使用）
- `agenda_gitea_url`：议程文件链接
- `meeting_dir`：会议目录名

## 错误处理规则

| 错误场景 | 处理方式 |
|----------|----------|
| 参数缺失 | 直接报错，不执行任何写入 |
| 腾讯会议信息缺失 | 直接报错，不创建 Gitea 目录 |
| Gitea 写入失败 | 报错，由 OpenClaw 决定是否回滚会议 |
| 仓库成员邮箱部分缺失 | 不阻塞主流程，返回能拿到的邮箱 |
| 上次会议摘要不可用 | agenda 中写“暂无参考记录” |

## 触发示例

| 用户说 | OpenClaw 应做什么 |
|--------|-------------------|
| "周三下午 3 点开个组会" | 解析时间 → 追问项目 → 创建腾讯会议 → 调本技能 → 调邮件 skill 发邀请 |
| "从下周一起每周一下午 3 点开例会连续 10 周" | 解析循环规则 → 追问项目 → 创建腾讯会议 → 调本技能 |
| "帮灵巧手项目约个会，后天上午 10 点" | 识别仓库 → 创建腾讯会议 → 调本技能 |
