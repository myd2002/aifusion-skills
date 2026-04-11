---
name: meeting-skill-a-create-meeting
version: 1.0.0
description: 根据自然语言创建腾讯会议，在对应 Gitea 仓库初始化会议目录，并发送邀请邮件
author: mayidan
license: MIT
required_environment_variables:
  - GITEA_BASE_URL
  - GITEA_TOKEN_BOT
  - AIFUSION_META_REPO
  - ANTHROPIC_API_KEY
  - ANTHROPIC_BASE_URL
  - MINIMAX_MODEL
  - SMTP_HOST
  - SMTP_PORT
  - SMTP_USER
  - SMTP_PASSWORD
  - TENCENT_MEETING_MODE
primary_credential: GITEA_TOKEN_BOT
credentials:
  - name: GITEA_TOKEN_BOT
    type: personal_access_token
    provider: gitea
  - name: ANTHROPIC_API_KEY
    type: api_key
    provider: minimax
  - name: SMTP_PASSWORD
    type: password
    provider: smtp
environment_file: ~/.config/meeting-skill-a-create-meeting/.env
---

# Skill-A: create_meeting

## 功能简介

`create_meeting` 用于根据用户的自然语言指令创建一次会议，并自动完成以下流程：

1. 解析用户意图，提取会议时间、主题、时长、周期性、与会人员、项目归属
2. 若项目归属不明确，返回追问信息给上层对话系统
3. 调用腾讯会议能力创建会议
4. 在对应 Gitea 仓库中创建会议目录 `meetings/YYYY-MM-DD-HHMM/`
5. 写入 `meta.yaml` 和 `agenda.md`
6. 查询与会成员邮箱并发送邀请邮件
7. 将本次操作写入 `aifusion-meta/logs/YYYY-MM-DD.jsonl`

## 适用场景

示例自然语言输入：

- 周三下午 3 点开组会
- 现在马上开个紧急会议讨论 v2 设计
- 从下周一起每周一下午 3 点开例会，连续 10 周
- 帮我创建一个灵巧手项目会议，明天下午 4 点，讨论触觉模块联调

## 输入参数

建议由 OpenClaw 或其他调度层传入以下参数。

### 必填参数

- `query`  
  用户自然语言原始指令

- `organizer`  
  组织者的 Gitea 用户名

### 选填参数

- `attendees`  
  逗号分隔的 Gitea 用户名列表，例如：`mayidan,sujinze,liuzhaolin`

- `repo_hint`  
  仓库提示，例如：`HKU-AIFusion/dexterous-hand`

- `topic_hint`  
  主题提示，用于在自然语言不清晰时辅助补充

## 输出说明

本 skill 标准输出为 JSON。

### 成功时返回

字段说明：

- `ok`: 是否成功，值为 `true`
- `status`: 固定为 `created`
- `repo`: 目标仓库
- `meeting_dir`: 会议目录名
- `meeting_id`: 腾讯会议内部 ID
- `meeting_code`: 腾讯会议号
- `join_url`: 入会链接
- `agenda_url`: agenda 文件链接
- `message`: 结果说明

示例：

    {
      "ok": true,
      "status": "created",
      "repo": "HKU-AIFusion/dexterous-hand",
      "meeting_dir": "2026-04-15-1500",
      "meeting_id": "xxx",
      "meeting_code": "123456789",
      "join_url": "https://meeting.tencent.com/...",
      "agenda_url": "http://43.156.243.152:3000/HKU-AIFusion/dexterous-hand/src/branch/main/meetings/2026-04-15-1500/agenda.md",
      "message": "会议已建立，已完成仓库初始化与邮件通知。"
    }

### 需要追问项目归属时返回

字段说明：

- `ok`: 为 `false`
- `status`: 固定为 `need_repo`
- `question`: 追问内容
- `repo_candidates`: 可选仓库列表
- `parsed_intent`: 当前已解析出的意图结果

示例：

    {
      "ok": false,
      "status": "need_repo",
      "message": "缺少项目归属，无法继续创建会议。",
      "stage": "resolve_repo",
      "question": "这是哪个项目的会议，还是跨项目会议？",
      "repo_candidates": [
        "HKU-AIFusion/dexterous-hand",
        "HKU-AIFusion/aifusion-meta"
      ]
    }

### 失败时返回

字段说明：

- `ok`: 为 `false`
- `status`: 固定为 `error`
- `stage`: 失败阶段
- `message`: 错误信息

示例：

    {
      "ok": false,
      "status": "error",
      "stage": "create_meeting",
      "message": "腾讯会议创建失败：..."
    }

## 调用方式

命令行示例：

    python main.py \
      --query "明天下午4点开灵巧手项目会议，讨论v2设计" \
      --organizer mayidan \
      --attendees "mayidan,sujinze,liuzhaolin"

## 工作流

本 skill 的主流程如下：

1. 接收用户输入参数
2. 调用 `parse_intent.py` 解析自然语言
3. 判断项目归属是否明确
4. 若不明确，则返回 `need_repo`
5. 若明确，则调用 `tencent_meeting.py` 创建会议
6. 调用 `gitea_ops.py` 生成会议目录与文件
7. 调用 `email_sender.py` 发送邀请邮件
8. 记录日志并返回结果

## 依赖说明

运行本 skill 需要以下能力支持：

- MiniMax API，用于意图解析
- Gitea Bot Token，用于仓库读写
- SMTP 邮件配置，用于发送邀请
- 腾讯会议桥接能力，用于创建会议

## 模块说明

本 skill 中：

- `tencent_meeting.py` 是腾讯会议调用适配层，不是重新实现腾讯会议 skill
- `email_sender.py` 是邮件发送模块，当前版本直接走 SMTP，更稳定，也更便于后续 Skill-B/C/D/H 复用

## 入口说明

本 skill 入口为：

    python main.py

OpenClaw 或其他调度器应向 `main.py` 传入参数，然后读取其标准输出 JSON 结果。