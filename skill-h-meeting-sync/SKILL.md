---
name: skill-h-meeting-sync
description: >
  cron 每30分钟触发。拉取腾讯会议未来7天列表与Gitea所有仓库
  scheduled/brief-sent状态会议做三向对比。处理新增（暂存pending+通知组织者确认归属）、
  取消（status→cancelled+全员通知）、改期（旧目录置rescheduled+新目录继承议程+全员通知）。
  归档status∈{cancelled,rescheduled}且超30天的历史目录。无需手动触发。
---

# Skill-H: meeting_sync

## 触发方式

cron 定时，每 30 分钟运行一次：

```
*/30 * * * * python -m skills.meeting_sync
```

或直接调用：

```bash
python main.py
```

## 完整流程

```
tencent_poller
  └─ GET /v1/meetings → 未来7天会议列表

gitea_state
  └─ 遍历所有受管仓库 meetings/*/meta.yaml
  └─ 筛选 status ∈ {scheduled, brief-sent}

diff_engine（三向对比，按 meeting_id 匹配）
  ├─ 新增：腾讯有 + Gitea 无
  ├─ 取消：Gitea 有 + 腾讯无
  ├─ 改期：两边都有，时间差 > 5 分钟
  └─ 一致：无需处理

handlers
  ├─ 新增 → 暂存 aifusion-meta/pending/MEETING_ID.yaml
  │          → 邮件通知 advisor 确认归属仓库
  ├─ 取消 → meta.yaml: status → cancelled
  │          → SMTP 全员通知
  ├─ 改期 → 旧目录: status → rescheduled
  │          → 新目录: status = scheduled（含 rescheduled_from 字段）
  │          → 继承 organizer / attendees / agenda 议题
  │          → SMTP 全员通知
  └─ 归档 → collect_archivable_meetings()
             → cancelled/rescheduled 且 > 30天
             → 逐文件复制到 meetings/archive/
             → 原目录 meta.yaml: status → archived

写汇总日志到 aifusion-meta/logs/YYYY-MM-DD.jsonl
```

## 与 Skill-B 的协作关系

| Skill | 频率 | 职责 |
|-------|------|------|
| Skill-B | 每15分钟 | 顺手同步（发简报时附带检测新会议） |
| Skill-H | 每30分钟 | 完整三向对比，处理取消/改期/归档 |

两者有意设计为冗余：Skill-B 的15分钟频率覆盖临时会议的快速响应，
Skill-H 的完整对比保证状态长期一致。

## 新增会议的归属确认流程

```
腾讯会议新增
  └─ Skill-H 检测到 → 暂存 pending/MEETING_ID.yaml
                    → 邮件通知 advisor
                    → advisor 在 OpenClaw 中回复归属
                    → Skill-A 的 --repo-reply 流程处理
                    → pending_store.delete_pending(meeting_id)
```

## 改期后的 meta.yaml 关键字段

```yaml
# 旧目录
status: rescheduled
rescheduled_at: "2026-04-22T10:00:00+08:00"
rescheduled_to: "2026-04-29 15:00"

# 新目录
status: scheduled
rescheduled_from: "2026-04-22-1500"   # Skill-B 看到此字段会跳过发简报
```

## 容错设计

- 腾讯会议 API 失败 → 跳过新增/取消/改期处理，仍执行归档清理
- 单条会议处理失败 → 记录日志继续处理下一条，不中断整体流程
- 归档文件复制失败 → 记录日志，原目录状态不变，下轮重试

## 产物

- `aifusion-meta/pending/MEETING_ID.yaml`：归属待确认的新增会议
- `aifusion-meta/logs/YYYY-MM-DD.jsonl`：汇总运行日志
- `meetings/archive/YYYY-MM-DD-HHMM/`：归档的历史会议目录
- 取消/改期通知邮件

## 依赖环境变量

见 env-example.txt（与前序 Skill 完全共享）