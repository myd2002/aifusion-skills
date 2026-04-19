---
name: skill-c-fetch-minutes
description: "【会后纪要抓取与issue草稿生成】每10分钟由 OpenClaw cron 触发一次。负责在会议结束后从腾讯会议拉取转录与AI智能纪要，由 OpenClaw 做两阶段issue抽取，生成 draft_issue.md 等四个文件并提交到 Gitea，最后通知组织者审核。不处理会议创建、会前简报、issue落地等场景。"
author: mayidan
---

# Skill-C: fetch_minutes — 会后纪要抓取与 issue 草稿生成

## 定位

本技能是 **cron 每 10 分钟触发的会后处理流程总控**。

职责分工：

- **OpenClaw（本次对话/cron 任务）**
  - 触发 scan，获取三类待处理会议
  - 判断 A 类会议是否已结束（时间判断 + 可选腾讯会议状态确认）
  - 调用 tencent-meeting-skill 拉取转录与 AI 智能纪要（三层降级）
  - 对 C 类会议，从 Gitea 读取已有 transcript.md 内容
  - **作为 AI 完成两阶段 issue 抽取**，生成四个文件的完整内容
  - 调用本技能各命令执行 Gitea 侧状态更新与文件提交
  - 调用 imap-smtp-email 向组织者发送审核通知邮件

- **Skill-C（本技能）**
  - `scan`：扫描所有仓库，按 A / B / C 三类分组返回待处理会议
  - `set-waiting`：将 brief-sent → waiting-transcript，记录轮询开始时间
  - `set-failed`：将 waiting-transcript → transcript-failed，返回通知邮件参数
  - `commit-content`：将四个文件写入 Gitea 会议目录，status → draft-pending-review，写日志，返回审核通知邮件参数

- **tencent-meeting-skill**
  - 拉取录制列表（`get_records_list`）获取 record_file_id
  - Layer 1：`get_smart_summary` + `get_transcripts_details`
  - Layer 2：`get_transcripts_details` 单独拉取
  - Layer 3：均失败则跳过，下次 cron 重试

- **imap-smtp-email**
  - 发送 transcript-failed 通知给组织者（附手动上传说明）
  - 发送 draft-pending-review 通知给组织者（附两种确认方式说明）

---

## 配置要求

配置文件：`~/.config/skill-c-fetch-minutes/.env`

首次使用前运行：

```bash
bash setup.sh
```

---

## 严格工作流（每次 cron 触发必须按顺序执行）

---

### 第一步：scan，获取三类待处理会议

```bash
node main.js scan
```

返回 JSON，包含三个字段：

- `class_a`：status == brief-sent 的会议（需判断是否已结束）
- `class_b`：status == waiting-transcript 的会议（需拉取转录内容，或判断超时）
- `class_c`：status == transcript-failed 且 transcript.md 已存在的会议（组织者已手动上传）

每条记录包含：
- `repo`、`meeting_dir`、`topic`、`organizer`、`organizer_email`
- `scheduled_time`、`duration_minutes`（用于判断是否结束）
- `meeting_id`、`meeting_code`（用于调用腾讯会议 API）
- `attendees`、`attendee_emails`
- `transcript_started_at`（仅 B 类，用于判断是否超时 60 分钟）
- `category`：single | cross-project

**如果三类均为空，本次 cron 无需继续。**

---

### 第二步：处理 A 类会议（status == brief-sent）

对每条 A 类会议：

#### 2a. 判断会议是否已结束

根据 scan 返回的 `scheduled_time` 和 `duration_minutes` 计算结束时间：

```
结束时间 = scheduled_time + duration_minutes 分钟
已结束 = 当前时间 > 结束时间
```

**未结束 → 跳过，等待下次 cron。**

**已结束 → 调用 set-waiting：**

```bash
node main.js set-waiting \
  --repo "HKU-AIFusion/dexterous-hand" \
  --meeting-dir "2026-04-22-1500"
```

返回成功后，将该会议加入本次 cron 的 B 类处理队列（立即继续处理，无需等待下次 cron）。

---

### 第三步：处理 B 类会议（status == waiting-transcript）

**注意：本步骤同时处理原 B 类和第二步刚转入的会议。**

对每条会议：

#### 3a. 判断是否超时

从 scan（或 set-waiting 返回）取 `transcript_started_at`，计算已等待时长：

```
已等待 = 当前时间 - transcript_started_at
超时 = 已等待 > 60 分钟
```

**超时 → 调用 set-failed，然后调用 imap-smtp-email 发送手动上传通知：**

```bash
node main.js set-failed \
  --repo "HKU-AIFusion/dexterous-hand" \
  --meeting-dir "2026-04-22-1500" \
  --organizer-email "organizer@163.com"
```

返回 `fail_email.to / .subject / .html`，OpenClaw 调用 imap-smtp-email 发送。**跳过后续步骤。**

#### 3b. 三层降级拉取转录内容

**先调用 tencent-meeting-skill 获取 record_file_id：**

```
调用 get_records_list，传入 meeting_id
→ 从返回结果中取第一条录制的 record_file_id
```

若 get_records_list 失败或返回空，整条跳过（Layer 3，等下次 cron）。

**Layer 1：AI 智能纪要 + 转录全文**

```
调用 get_smart_summary，传入 record_file_id → 得到 ai_summary 文本
调用 get_transcripts_details，传入 record_file_id → 得到 transcript 文本
```

两者都成功 → `source = "ai_summary"`，继续第四步。

**Layer 2：仅转录全文**

Layer 1 失败 → 只调用 `get_transcripts_details`：

```
调用 get_transcripts_details，传入 record_file_id → 得到 transcript 文本
```

成功 → `source = "transcript_only"`，ai_summary 置空，继续第四步。

**Layer 3：均失败**

Layer 2 也失败 → 整条跳过，等下次 cron（不修改 Gitea 状态，下次仍为 waiting-transcript）。

---

### 第四步：处理 C 类会议（transcript-failed，transcript.md 已存在）

对每条 C 类会议：

从 Gitea 会议目录读取 `transcript.md` 内容（scan 已确认文件存在）：

```
source = "manual_upload"
transcript = transcript.md 内容
ai_summary = 空（无 AI 智能纪要）
```

直接进入第五步，OpenClaw 做 AI 抽取。

---

### 第五步：OpenClaw 完成两阶段 AI 抽取

**此步骤 OpenClaw 作为 AI 负责全部生成，不调用任何 skill 脚本。**

输入：`transcript`（转录文本）、`ai_summary`（可能为空）、`source`、会议基本信息（topic / meeting_dir / attendees / scheduled_time）

#### 阶段一：结构化抽取

基于 ai_summary（优先）和 transcript，严格按以下 JSON 格式输出，**只输出 JSON，不加任何说明文字或 markdown 标记**：

```json
{
  "decisions": [
    { "content": "决策内容", "owner": "负责人（Gitea 用户名或姓名）" }
  ],
  "action_items": [
    {
      "task": "任务描述",
      "assignee_hint": "提到的人名或用户名",
      "due_date_hint": "截止时间表述，如'下周五'",
      "depends_on_hint": "依赖描述，如'等 sujinze 出图后'",
      "quote": "转录中最相关的原话片段"
    }
  ],
  "open_questions": [
    { "content": "待讨论问题" }
  ],
  "notes": "其他补充说明"
}
```

**严格约束：严禁编造转录中没有的内容。任务描述含糊时宁可跳过，不写。**

#### 阶段二：映射与回填

基于阶段一结果：

- `assignee_hint` → 映射为 Gitea 用户名（参考 attendees 列表：`mayidan, sujinze, liuzhaolin, xiongzihan, yangyaojun, wulvzhen, zhoulihao`）
- `due_date_hint` → 转换为 `YYYY-MM-DD` 格式（基于会议日期推算）
- `quote` → 在 transcript 中搜索最相近的原话，若找不到则置空
- `depends_on_hint` → 只有原文出现明确依赖表述时才保留，否则置空

---

### 第六步：生成四个文件内容

OpenClaw 根据两阶段抽取结果，生成以下四个文件的完整文本内容，**分别写入临时文件**：

#### transcript.md → `/tmp/transcript_{meeting_dir}.md`

直接写入转录原文（来自腾讯会议 API 或手动上传内容）：

```markdown
# 会议转录 — {topic}

**来源**：{source}（ai_summary / transcript_only / manual_upload）
**会议时间**：{scheduled_time}
**导出时间**：{当前时间}

---

{transcript 原文}
```

#### ai_summary.md → `/tmp/ai_summary_{meeting_dir}.md`（仅 source=ai_summary 时生成）

```markdown
# AI 智能纪要 — {topic}

**会议时间**：{scheduled_time}

---

{ai_summary 原文}
```

#### minutes.md → `/tmp/minutes_{meeting_dir}.md`

```markdown
# 会议纪要 — {topic}

**会议时间**：{scheduled_time}
**与会人员**：{attendees}
**记录生成时间**：{当前时间}

## 决策记录

{decisions 列表，每条格式：- [决策内容]（负责人：@username）}

## 待讨论问题

{open_questions 列表，每条格式：- [问题]}

## 补充说明

{notes}
```

#### draft_issue.md → `/tmp/draft_issue_{meeting_dir}.md`

```markdown
# issue 草稿 — {topic} · {meeting_dir}

> **待组织者审核。** 确认方式：
> 1. 在 Gitea 中将本文件改名为 `confirmed_issue.md`
> 2. 或在 OpenClaw 中说"确认 {meeting_dir} 的 issue"
>
> **注意**：跨项目会议确认后不自动建 issue，仅发邮件建议。

## 待办事项

{对每条 action_item：}
- [ ] **{task}**
  - 负责人：@{assignee}（原文：{assignee_hint}）
  - 截止日期：{due_date}
  - 依赖：{depends_on 或"无"}
  - 原话引用：> "{quote}"

## 无待办事项说明

（action_items 为空时，此处说明"本次会议未提取到明确的待办任务，请组织者根据会议纪要自行处理"）
```

---

### 第七步：调用 commit-content 提交文件并更新状态

```bash
node main.js commit-content \
  --repo "HKU-AIFusion/dexterous-hand" \
  --meeting-dir "2026-04-22-1500" \
  --topic "v2 设计评审" \
  --organizer-email "organizer@163.com" \
  --transcript-file "/tmp/transcript_2026-04-22-1500.md" \
  --minutes-file "/tmp/minutes_2026-04-22-1500.md" \
  --draft-issue-file "/tmp/draft_issue_2026-04-22-1500.md" \
  [--ai-summary-file "/tmp/ai_summary_2026-04-22-1500.md"] \
  [--source "ai_summary"]
```

`--ai-summary-file` 仅在 source=ai_summary 时传入，其余情况不传。

返回 JSON，关键字段：
- `success`
- `review_email.to` / `.subject` / `.html`：发给组织者的审核通知邮件参数
- `gitea_dir_url`：会议目录在 Gitea 的链接（方便组织者直接跳转）

---

### 第八步：调用 imap-smtp-email 发送审核通知

使用 commit-content 返回的 `review_email` 字段：

- 收件人：`review_email.to`（组织者）
- 主题：`review_email.subject`
- 正文：`review_email.html`

---

## 错误处理规则

| 错误场景 | 处理方式 |
|----------|----------|
| scan 失败 | 打印错误，本次 cron 中止 |
| 会议尚未结束 | 跳过，等待下次 cron |
| get_records_list 失败 | Layer 3，整条跳过 |
| Layer 1 失败，Layer 2 成功 | 继续，source=transcript_only |
| Layer 1/2 均失败 | Layer 3，整条跳过，状态保持 waiting-transcript |
| AI 抽取失败 | 跳过本条，记录错误，下次 cron B类重试 |
| commit-content 写 Gitea 失败 | 不发邮件，下次 cron 重试（状态未变） |
| 邮件发送失败 | 记录错误，但 status 已更新（日志标注 email_failed） |
| 超时 60 分钟 | set-failed，发手动上传说明邮件 |

---

## 幂等性说明

- scan 只返回 brief-sent / waiting-transcript / transcript-failed+transcript.md 的会议
- set-waiting / set-failed 均有幂等检查，重复调用安全
- commit-content 成功后 status → draft-pending-review，下次 cron 不再扫描到
- commit-content 对已存在的文件执行 update（而非 create），避免重复提交报错
