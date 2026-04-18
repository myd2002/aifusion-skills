---
name: skill-d-create-issues
description: >
  事件驱动。组织者确认issue草稿后触发：方式A=组织者在Gitea网页把
  draft_issue.md改名为confirmed_issue.md触发webhook；方式B=组织者在
  OpenClaw中说"确认YYYY-MM-DD-HHMM的issue"直接调用CLI模式。
  单项目会议批量创建Gitea issue（两轮：建issue+回填依赖），发全员minutes邮件和个人任务通知。
  跨项目会议不建issue，仅向组织者发建议邮件。
---

# Skill-D: create_issues

## 触发方式

### 方式 A：Gitea Webhook（推荐）

组织者在 Gitea 网页将文件重命名：
```
meetings/YYYY-MM-DD-HHMM/draft_issue.md
→ meetings/YYYY-MM-DD-HHMM/confirmed_issue.md
```
Gitea push webhook 自动触发，无需其他操作。

**Webhook 服务启动（服务器后台常驻）：**
```bash
python main.py --mode webhook
# 或使用 systemd / supervisor 守护进程
```

**Gitea 每个受管仓库需配置：**
```
URL:    http://43.156.243.152:{WEBHOOK_PORT}/webhook
Secret: 与 WEBHOOK_SECRET 环境变量一致
事件:   Push Events（仅 push）
```

### 方式 B：OpenClaw 对话

组织者说："确认 2026-04-22-1500 的 issue"

OpenClaw 调用：
```bash
python main.py --mode cli \
  --repo HKU-AIFusion/dexterous-hand \
  --meeting-dir 2026-04-22-1500
```

## 处理流程

```
入参校验
  └─ meta.yaml status == draft-pending-review？
       否 → 幂等拒绝（minutes-published）或报错

读取 confirmed_issue.md → issue_parser 解析
  └─ 解析失败 → 邮件通知组织者，保持 draft-pending-review

判断会议类型（meta.meeting_category）
  ├─ single-project →
  │   第一轮：逐条创建 Gitea issue
  │     labels: [meeting-action, meeting:YYYY-MM-DD-HHMM]
  │     正文:   任务 / 负责人 / 截止日期 / 原话引用 / 会议链接
  │   第二轮：回填 depends-on 关系（追加评论）
  │   发送全员 minutes 邮件
  │   发送个人任务通知邮件（每个 assignee 单独发）
  └─ cross-project →
      不建 issue
      仅向组织者发 issue 建议邮件

meta.yaml: status → minutes-published
写日志
```

## 幂等性

- status != draft-pending-review 时直接拒绝，不重复建 issue
- Webhook 重复触发（Gitea 重试）不会重复建 issue

## 错误处理

| 错误场景 | 处理方式 |
|---------|---------|
| confirmed_issue.md 解析失败 | 邮件通知组织者，保持 draft-pending-review |
| 某条 issue 创建失败 | 继续创建其他，失败清单发邮件给组织者 |
| 依赖回填失败 | 仅写日志警告，不影响 issue 本身 |
| 邮件发送失败 | 不阻塞主流程，记录日志 |

## 状态流转

```
draft-pending-review
  └─(组织者确认)→ minutes-published  ✅ 终态
```

## 产物

- Gitea issue（单项目会议，每条 action item 对应一个）
- label: meeting-action + meeting:YYYY-MM-DD-HHMM
- 全员 minutes 邮件
- 个人任务通知邮件（每个 assignee）
- meta.yaml: created_issues 字段记录 local_id → issue_number 映射

## 依赖环境变量

见 env-example.txt