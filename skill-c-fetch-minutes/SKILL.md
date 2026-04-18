---
name: skill-c-fetch-minutes
description: >
  cron 每 10 分钟触发。遍历所有受管仓库，扫描三类会议状态并推进流程：
  A类(brief-sent已结束)推进到waiting-transcript；
  B类(waiting-transcript)三层降级拉取腾讯会议转录与AI智能纪要，
  MiniMax两阶段抽取生成draft_issue.md，通知组织者审核；
  C类(transcript-failed且已手动上传)直接进入抽取流程。
  无需手动触发。
---

# Skill-C: fetch_minutes

## 触发方式

cron 定时，每 10 分钟运行一次：

```
*/10 * * * * python -m skills.fetch_minutes
```

或直接调用：

```bash
python main.py
```

## 三类会议状态

| 类型 | 当前 status | 触发条件 | 处理结果 |
|------|------------|---------|---------|
| A 类 | brief-sent | 会议已结束 | → waiting-transcript |
| B 类 | waiting-transcript | 未超时 / 已超时 | 拉取内容→抽取→draft-pending-review / → transcript-failed |
| C 类 | transcript-failed | 已手动上传 transcript.md | 读取文件→抽取→draft-pending-review |

## 三层降级策略

```
Layer 1: AI 智能纪要 + 转录原文   → source: ai_summary
Layer 2: 仅转录原文               → source: transcript_only
Layer 3: 均失败                   → 保持 waiting-transcript，等下轮重试
超时(>60分钟): → transcript-failed，邮件通知组织者手动上传
```

## MiniMax 两阶段抽取

```
阶段 1: 结构化抽取
  输入: ai_summary(优先) + transcript
  输出: decisions / action_items / open_questions / notes
  约束: 严禁编造；quote 必须是原话；描述含糊则跳过

阶段 2: 字段映射
  assignee_hint → Gitea 用户名（精确→包含→模糊匹配）
  due_date_hint → YYYY-MM-DD
  transcript 全文搜索最相近原话 → quote
  明确依赖表达才设置 depends_on
```

## 产物文件

```
meetings/YYYY-MM-DD-HHMM/
├── transcript.md         # 转录原文（溯源依据）
├── ai_summary.md         # AI 智能纪要（Layer 1 成功时）
├── minutes.md            # 正式会议纪要
└── draft_issue.md        # 待审核 issue 草稿
```

## draft_issue.md 结构

每条 action item 包含：
- 勾选框 + 任务描述
- 负责人（@Gitea用户名）
- 截止日期（YYYY-MM-DD）
- 依赖关系（#local_id）
- 原话引用（> 转录原句）

## 幂等性

- 所有文件创建前检查是否已存在，已存在则跳过
- status 校验保证 B 类超时处理不重复触发
- draft_issue.md 写入成功后才更新 status，失败则保持原状

## 状态流转

```
brief-sent
  └─(会议结束)→ waiting-transcript
                  ├─(内容可用)→ draft-pending-review  ← 通知组织者审核
                  ├─(超时60min)→ transcript-failed     ← 通知组织者手动上传
                  └─(内容未就绪)→ 保持，等下轮重试
transcript-failed
  └─(手动上传transcript.md)→ draft-pending-review     ← 通知组织者审核
```

## 依赖环境变量

见 env-example.txt（在 Skill-A 基础上新增腾讯会议 API 配置）