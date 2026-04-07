---
name: gitea-progress-report
version: 1.2.0
description: 获取 Gitea 各仓库提交记录，生成进度报告并发邮件给管理员
author: mayidan
---

# Gitea Progress Report

## 功能描述
对每个 Gitea 可见仓库分别生成一份进度报告，内容包括：
- AI 综合评估（整体一段话）
- 成员贡献排行（带工作量占比）
- 每位成员的工作摘要、具体提交记录、改动文件
- 风险提示（低活跃成员、模糊提交等）

每个仓库单独发送一封邮件给该仓库的管理员。

## 使用场景
- 当用户说"帮我生成进度报告"、"发送周报"、"发送日报"时触发
- 定时任务：每天或每周自动生成并发送报告
- 当用户想了解团队最近工作情况时

## 使用方法

```bash
# 对所有可见仓库生成报告（过去7天）
python scripts/generate_report.py --hours 168

# 对所有可见仓库生成报告（过去24小时）
python scripts/generate_report.py --hours 24

# 对指定仓库生成报告
python scripts/generate_report.py --hours 168 --repo mayidan/project-test
```

## 执行流程

**重要：必须严格按照以下步骤执行，不得跳过任何步骤。**

### 第一步：运行脚本获取数据（必须执行）

无论何时触发此 skill，必须首先运行以下命令获取最新数据，不得使用记忆中的历史数据：

```bash
python scripts/generate_report.py --hours 168
```

### 第二步：从脚本输出中读取数据

脚本输出是一个 JSON 数组，每个元素包含：
- `repo`：仓库名称
- `admin_email`：仓库创建者邮箱
- `has_commits`：本期是否有提交记录
- `time_range`：统计周期描述
- `generated_at`：生成时间
- `overview`：总览数据
- `members`：各成员数据
- `vague_commits`：模糊提交列表

### 第三步：对每个仓库单独处理（每个仓库必须单独发一封邮件）

对 JSON 数组中的每一个仓库，分别执行以下操作，不得合并：

**情况一：`has_commits` 为 false（本期无提交）**

直接调用 imap-smtp-email skill 发送以下说明性邮件：
- 收件人：`admin_email` 字段（除非用户在对话中指定了其他收件人）
- 邮件主题：【项目进度报告】{repo} · {time_range}
- 邮件正文：
```
【项目进度报告】{repo} · {time_range}

本统计周期内该仓库暂无任何提交记录。

由 AIFusionBot 自动生成 · {generated_at}
```

**情况二：`has_commits` 为 true（本期有提交）**

按以下格式生成报告文本，然后调用 imap-smtp-email skill 发送邮件：
- 收件人：默认使用 `admin_email` 字段。如果用户在对话中指定了收件人，则以用户指定的为准。
- 邮件主题：【项目进度报告】{repo} · {time_range}
- 邮件正文：下方格式生成的完整报告

报告格式：

```
📊 项目进度报告
{repo} · {generated_at 只取日期}

🕐 统计周期：{time_range}
🔢 提交次数：{total_commits} 次
👥 参与成员：{total_members} 人
📈 新增代码：+{total_additions} 行　📉 删除：-{total_deletions} 行


🤖 AI 综合评估

  {根据提交内容和成员活跃度，用2-4句话综合评估本期项目进展。
  重点说明整体进展、主要推进了哪些工作、有无值得关注的问题。
  语言简洁专业。}


🏆 成员贡献排行

  🥇 {成员名}　{提交次数}次提交　+{additions}/-{deletions}行　{进度条} {百分比}%
  🥈 {成员名}　{提交次数}次提交　+{additions}/-{deletions}行　{进度条} {百分比}%
  🥉 {成员名}　{提交次数}次提交　+{additions}/-{deletions}行　{进度条} {百分比}%
  🔹 {成员名}　{提交次数}次提交　+{additions}/-{deletions}行　{进度条} {百分比}%

  进度条规则：总长度10格，用█表示占比，空格补齐


👤 成员详情

▌{成员名}　{进度判断}　提交 {X} 次
  🌿 活跃分支：{branches}
  💡 {根据该成员所有commit message提炼的一句话工作总结}

  📝 提交记录

    🕐 {MM-DD HH:mm}　{commit message}
      📁 {filename}(+{additions}/-{deletions})
      📁 {filename}(+{additions}/-{deletions})

    🕐 {MM-DD HH:mm}　{commit message}
      📁 {filename}(+{additions}/-{deletions})


▌{下一个成员，格式同上}


⚠️ 风险提示

  {有以下情况则列出，没有则写"✅ 本期暂无风险提示"：}
  ⚠️ {成员名} 本期仅提交 {X} 次，建议关注进度
  ⚠️ 发现 {X} 条模糊提交（如"fix"、"update"），建议规范提交信息
  ℹ️ {其他值得注意的情况}


🤖 由 AIFusionBot 自动生成 · {generated_at}
🔗 {GITEA_URL}/{repo}
```

## 依赖 skill
- imap-smtp-email：发送邮件，请确保该 skill 已安装并配置好 SMTP 信息