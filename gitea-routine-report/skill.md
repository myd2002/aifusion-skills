---
name: gitea-routine-report
version: 2.1.0
description: 获取 Gitea 各仓库提交记录，调用 AI 生成进度报告，并发送 HTML 邮件给仓库管理员
author: mayidan, zhangyiwen
license: MIT
required_environment_variables:
  - GITEA_URL
  - GITEA_TOKEN
primary_credential: GITEA_TOKEN
credentials:
  - name: GITEA_TOKEN
    type: personal_access_token
    provider: gitea
environment_file: ~/.config/gitea-routine-report/.env
---

# Gitea Routine Report

## 功能描述
对每个 Gitea 可见仓库分别生成一份 HTML 格式进度报告，内容包括：
- 情况总览（统计周期、提交次数、参与成员）
- AI 综合评估（现状评估 + 下一步建议）
- 成员贡献排行
- 每位成员的工作摘要、文件类型分布、详细提交记录
- 本期无提交成员名单（含连续未提交天数和最后提交日期）
- 风险提示

每个仓库单独发送一封 HTML 邮件给该仓库的管理员。

## 使用场景
- 当用户说"帮我生成进度报告"、"发送周报"、"发送日报"时触发
- 当用户说"帮我生成某个仓库的进度报告"并明确给出仓库名时触发单仓库模式
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

无论何时触发此 skill，必须首先根据用户输入选择以下命令之一获取最新数据，不得使用记忆中的历史数据：

- 用户明确指定仓库时：
```bash
python scripts/generate_report.py --hours 168 --repo owner/repo
```

- 用户未指定仓库时：
```bash
python scripts/generate_report.py --hours 168
```

`--hours` 可根据用户要求替换（例如 `24`）。

### 第二步：从脚本输出中读取数据

脚本输出是一个 JSON 数组，每个元素包含：
- `repo`：仓库名称
- `admin_email`：仓库创建者邮箱
- `has_commits`：本期是否有提交记录
- `time_range`：统计周期简述
- `time_range_detail`：统计周期详细时间范围
- `generated_at`：生成时间
- `overview`：总览数据（total_commits, total_members, total_deletions）
- `members`：各成员数据（含 commit_details, file_type_summary, branches）
- `inactive_members`：本期无提交成员列表（含 name, last_commit_date, inactive_days）
- `vague_commits`：模糊提交列表

### 第三步：对每个仓库单独生成 HTML 报告并发邮件

对 JSON 数组中的每一个仓库，分别执行以下操作，不得合并。

**情况一：`has_commits` 为 false（本期无提交）**

调用 imap-smtp-email skill 发送邮件：
- 收件人：`admin_email`（除非用户指定了其他收件人）
- 邮件主题：【项目进度报告】{repo} · {time_range}
- 邮件格式：HTML
- 邮件正文 HTML：

```html
<div style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;padding:24px;color:#333;">
  <h1 style="font-size:22px;color:#1a1a2e;border-bottom:3px solid #4a90d9;padding-bottom:10px;">
    📊 项目进度报告
  </h1>
  <p style="color:#666;margin-top:4px;">{repo} · {generated_at}</p>
  <div style="background:#fff3cd;border-left:4px solid #ffc107;padding:16px;border-radius:4px;margin:24px 0;">
    <strong>本统计周期内该仓库暂无任何提交记录。</strong><br>
    <span style="color:#666;font-size:13px;">统计周期：{time_range_detail}</span>
  </div>
  <p style="color:#999;font-size:12px;margin-top:32px;border-top:1px solid #eee;padding-top:12px;">
    🤖 由 AIFusionBot 自动生成 · {generated_at}<br>
    🔗 <a href="{GITEA_URL}/{repo}">{GITEA_URL}/{repo}</a>
  </p>
</div>
```

**情况二：`has_commits` 为 true（本期有提交）**

生成完整 HTML 报告，调用 imap-smtp-email skill 发送邮件：
- 收件人：默认使用 `admin_email`。用户指定了收件人则以用户指定为准。
- 邮件主题：【项目进度报告】{repo} · {time_range}
- 邮件格式：HTML
- 邮件正文：按以下模板生成完整 HTML

```html
<div style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;padding:24px;color:#333;">

  <!-- 标题 -->
  <h1 style="font-size:22px;color:#1a1a2e;border-bottom:3px solid #4a90d9;padding-bottom:10px;">
    📊 项目进度报告
  </h1>
  <p style="color:#666;margin-top:4px;">{repo} · {generated_at 只取日期}</p>

  <!-- 情况总览 -->
  <h2 style="font-size:17px;color:#4a90d9;margin-top:28px;">◆ 情况总览</h2>
  <table style="width:100%;border-collapse:collapse;font-size:14px;">
    <tr>
      <td style="padding:8px 12px;background:#f5f8ff;border:1px solid #e0e8f5;width:40%;"><strong>统计周期</strong></td>
      <td style="padding:8px 12px;border:1px solid #e0e8f5;">{time_range_detail}</td>
    </tr>
    <tr>
      <td style="padding:8px 12px;background:#f5f8ff;border:1px solid #e0e8f5;"><strong>提交次数</strong></td>
      <td style="padding:8px 12px;border:1px solid #e0e8f5;">{total_commits} 次</td>
    </tr>
    <tr>
      <td style="padding:8px 12px;background:#f5f8ff;border:1px solid #e0e8f5;"><strong>参与成员</strong></td>
      <td style="padding:8px 12px;border:1px solid #e0e8f5;">{total_members} 人</td>
    </tr>
  </table>

  <!-- AI 综合评估 -->
  <h2 style="font-size:17px;color:#4a90d9;margin-top:28px;">🤖 AI 综合评估</h2>
  <div style="background:#f0f7ff;border-left:4px solid #4a90d9;padding:16px;border-radius:4px;">
    <p style="margin:0 0 10px 0;"><strong>现状：</strong>{根据提交内容和成员活跃度，用2-3句话评估本期项目整体进展和主要推进了哪些工作}</p>
    <p style="margin:0;"><strong>下一步建议：</strong>{根据现状和风险，给项目负责人1-2条具体可执行的建议，帮助推进后续工作}</p>
  </div>

  <!-- 成员贡献排行 -->
  <h2 style="font-size:17px;color:#4a90d9;margin-top:28px;">🏆 成员贡献排行</h2>
  <table style="width:100%;border-collapse:collapse;font-size:14px;">
    <tr style="background:#4a90d9;color:white;">
      <th style="padding:8px 12px;text-align:left;">排名</th>
      <th style="padding:8px 12px;text-align:left;">成员</th>
      <th style="padding:8px 12px;text-align:left;">提交次数</th>
      <th style="padding:8px 12px;text-align:left;">删除行数</th>
      <th style="padding:8px 12px;text-align:left;">占比</th>
    </tr>
    <!-- 对每位成员按提交次数从高到低输出一行，排名图标：1=🥇 2=🥈 3=🥉 4+=🔹 -->
    <tr style="background:#f9f9f9;">
      <td style="padding:8px 12px;border:1px solid #eee;">🥇 #1</td>
      <td style="padding:8px 12px;border:1px solid #eee;"><strong>{成员名}</strong></td>
      <td style="padding:8px 12px;border:1px solid #eee;">{提交次数} 次</td>
      <td style="padding:8px 12px;border:1px solid #eee;">-{deletions} 行</td>
      <td style="padding:8px 12px;border:1px solid #eee;">
        <div style="background:#e0e8f5;border-radius:4px;height:14px;width:100px;display:inline-block;vertical-align:middle;">
          <div style="background:#4a90d9;border-radius:4px;height:14px;width:{百分比}px;"></div>
        </div>
        {百分比}%
      </td>
    </tr>
    <!-- 其余成员重复以上 tr，偶数行 background 改为 #ffffff -->
  </table>

  <!-- 成员详情 -->
  <h2 style="font-size:17px;color:#4a90d9;margin-top:28px;">👤 成员详情</h2>

  <!-- 对每位成员输出以下块 -->
  <div style="border:1px solid #e0e8f5;border-radius:6px;padding:16px;margin-bottom:16px;">
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <h3 style="margin:0;font-size:15px;color:#1a1a2e;">👤 {成员名}</h3>
      <span style="font-size:13px;">{进度判断} &nbsp; 提交 {X} 次</span>
    </div>
    <p style="margin:8px 0 4px 0;font-size:13px;color:#666;">
      🌿 活跃分支：{branches} &nbsp;|&nbsp;
      📁 改动文件类型：代码 {code}个 / 文档 {doc}个 / 数据 {data}个 / 图片 {image}个 / 其他 {other}个
    </p>
    <div style="background:#f5f8ff;border-left:3px solid #4a90d9;padding:10px 14px;border-radius:4px;margin:10px 0;font-size:14px;">
      💡 <strong>工作摘要：</strong>{根据该成员所有commit message提炼的一句话工作总结}
    </div>

    <p style="margin:10px 0 6px 0;font-size:13px;color:#666;"><strong>📝 提交记录（共 {X} 次）</strong></p>
    <!-- 对每条提交输出以下块 -->
    <div style="border-left:2px solid #e0e8f5;padding:8px 12px;margin-bottom:8px;font-size:13px;">
      <div style="color:#888;">🕐 {时间 YYYY-MM-DD HH:mm}</div>
      <div style="margin:4px 0;"><strong>{commit message}</strong></div>
      <!-- 对每个改动文件输出一行 -->
      <div style="color:#666;">📁 {filename} &nbsp;
        <span style="color:#e74c3c;">-{deletions}</span>
      </div>
    </div>
    <!-- 其余提交重复以上 div -->
  </div>
  <!-- 其余成员重复以上大 div -->

  <!-- 本期无提交成员 -->
  {如果 inactive_members 不为空则输出以下块，否则不输出}
  <h2 style="font-size:17px;color:#4a90d9;margin-top:28px;">😴 本期无提交成员</h2>
  <table style="width:100%;border-collapse:collapse;font-size:14px;">
    <tr style="background:#f5f5f5;">
      <th style="padding:8px 12px;text-align:left;border:1px solid #eee;">成员</th>
      <th style="padding:8px 12px;text-align:left;border:1px solid #eee;">连续未提交天数</th>
      <th style="padding:8px 12px;text-align:left;border:1px solid #eee;">上次提交日期</th>
    </tr>
    <!-- 对每个 inactive_member 输出一行 -->
    <tr>
      <td style="padding:8px 12px;border:1px solid #eee;">{name}</td>
      <td style="padding:8px 12px;border:1px solid #eee;color:#e74c3c;"><strong>{inactive_days} 天</strong></td>
      <td style="padding:8px 12px;border:1px solid #eee;">{last_commit_date}</td>
    </tr>
  </table>

  <!-- 风险提示 -->
  <h2 style="font-size:17px;color:#4a90d9;margin-top:28px;">⚠️ 风险提示</h2>
  <div style="background:#fff8f0;border-left:4px solid #f39c12;padding:16px;border-radius:4px;font-size:14px;">
    {有以下情况则列出，没有则输出：<span style="color:green;">✅ 本期暂无风险提示</span>}
    <!-- 每条风险单独一行 -->
    <div>⚠️ {成员名} 已连续 {inactive_days} 天未提交，上次提交日期：{last_commit_date}</div>
    <div>⚠️ 发现 {X} 条模糊提交（如"fix"、"update"），建议规范提交信息</div>
    <div>ℹ️ {其他值得注意的情况}</div>
  </div>

  <!-- 页脚 -->
  <p style="color:#999;font-size:12px;margin-top:32px;border-top:1px solid #eee;padding-top:12px;">
    🤖 由 AIFusionBot 自动生成 · {generated_at}<br>
    🔗 <a href="{GITEA_URL}/{repo}" style="color:#4a90d9;">{GITEA_URL}/{repo}</a>
  </p>

</div>
```

## 依赖 skill
- imap-smtp-email：发送邮件，请确保该 skill 已安装并配置好 SMTP 信息