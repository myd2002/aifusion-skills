---
name: get-gitea-commits
version: 1.0.0
description: 获取 Gitea 仓库的提交记录
author: mayidan
---

# Get Gitea Commits

## 功能描述
获取 Gitea 上所有可见仓库（公共仓库 + 已加入的私有仓库）的提交记录。
支持两种查询模式：按条数获取最近N条，或按时间段获取某段时间内的提交。

## 使用场景
- 当用户想了解最近有哪些代码提交时
- 当用户想知道某个仓库最近的进展时
- 当用户想查看某段时间内团队的提交情况时
- 定时任务：每天自动汇总昨天所有仓库的提交记录

## 使用方法
```bash
# 获取所有仓库最近10条提交
python scripts/get_commits.py --mode recent --limit 10

# 获取所有仓库最近24小时的提交
python scripts/get_commits.py --mode timerange --hours 24

# 获取指定仓库最近10条提交
python scripts/get_commits.py --mode recent --limit 10 --repo mayidan/project-test

# 获取指定仓库某时间段的提交
python scripts/get_commits.py --mode timerange --hours 48 --repo mayidan/project-test
```

## 参数说明
- `--mode`：查询模式，`recent`（按条数）或 `timerange`（按时间段）
- `--limit`：获取条数，mode为recent时使用，默认10
- `--hours`：获取多少小时内的提交，mode为timerange时使用，默认24
- `--repo`：指定仓库，格式为 `owner/reponame`，不填则获取所有可见仓库

## 返回内容
每条提交包含：
- 仓库名称
- 分支名称（branch）
- 提交者姓名
- 提交时间
- commit message
- is_vague：message 是否模糊（影响 diff 详细程度）
- stats：总新增行数、总删除行数、改动文件数
- parent_shas：父提交 SHA，供报告生成器分析连续提交的关联性
- files：改动文件列表
  - 文件名、状态（added/modified/deleted）、新增行数、删除行数
  - diff（智能模式）：
    - message 清晰 → 只返回前10行预览 + 改动的函数名
    - message 模糊 → 返回完整 diff