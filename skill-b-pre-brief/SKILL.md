---
name: skill-b-pre-brief
description: >
  cron 每 15 分钟自动触发。遍历所有受管仓库，筛选距会议开始 30 分钟至 4 小时内、
  status==scheduled 的会议，自动生成按人汇总的会前简报（pre_brief.md）并发送邮件给全员。
  无需手动触发。改期会议（含 rescheduled_from 字段）自动跳过，直接置 brief-sent。
---

# Skill-B: pre_brief

## 触发方式

cron 定时，每 15 分钟运行一次：

```
*/15 * * * * python -m skills.pre_brief
```

或直接调用：

```bash
python main.py
```

## 工作流

```
repo_scanner
  └─ 扫描所有仓库 meetings/*/meta.yaml
  └─ 筛选 status==scheduled 且时间在 [now+30min, now+4h] 内的会议

window_calculator
  └─ 循环会议 → 取上一次同 series_id 会议时间到现在
  └─ 临时会议 → 取同仓库最近一次会议时间到现在
  └─ 无历史   → now-7d 到 now

activity_fetcher（复用 gitea-routine-report 的 get_commits_by_repo 逻辑）
  └─ 按成员拉取 commits / issues / PRs / 未完成 meeting-action

brief_generator（MiniMax 生成摘要 + 渲染 pre_brief.md）
  └─ 每人 3-5 条 bullet point，严禁编造
  └─ 合成含 Commits 折叠明细的 pre_brief.md

→ commit pre_brief.md 到 Gitea 会议目录
→ SMTP 发送简报邮件给全员
→ meta.yaml: status → brief-sent
→ 写日志
```

## 幂等性

- status 一旦变为 brief-sent，后续扫描不会再次命中
- pre_brief.md 已存在时跳过创建，不重复 commit

## 产物

```
meetings/YYYY-MM-DD-HHMM/
├── meta.yaml        # status 更新为 brief-sent
└── pre_brief.md     # 会前简报（含各成员 AI 摘要 + Commits 明细）
```

## 与 gitea-routine-report 的复用关系

| 模块 | 复用来源 |
|------|---------|
| commits 拉取逻辑 | activity_fetcher.py 完整移植自 get_commits.py |
| 成员统计（提交数/文件类型分布/活跃度标签） | 移植自 generate_report.py 的 build_summary() |
| MiniMax 摘要 prompt 风格 | 与 routine-report AI 分析部分保持一致 |
| 输出格式 | 改为 markdown（而非 HTML 邮件），新增 meeting-action 未完成任务展示 |

## 依赖环境变量

见 env-example.txt（与 Skill-A 共享同一份）