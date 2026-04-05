---
name: get-gitea-repo-structure
version: 1.0.0
description: 获取 Gitea 仓库的完整文件树快照
author: mayidan
---

# Get Gitea Repo Structure

## 功能描述
获取指定 Gitea 仓库在某一时刻的完整文件树快照。
返回仓库内所有文件的清单，包含文件类型、路径、大小，
并自动对文件进行分类（核心代码、文档、附件等）。

## 使用场景
- 生成项目报告时，需要了解仓库当前的交付目录
- 分析项目结构是否完整（是否有 README、文档、测试等）
- 识别哪些是核心代码文件，哪些是附件（PDF/PPT/图片）
- 配合 get-gitea-commits 对每个文件贴上贡献者标签

## 使用方法
```bash
# 获取指定仓库的完整文件树（默认主分支最新状态）
python scripts/get_repo_structure.py --repo mayidan/project-test

# 获取指定分支的文件树
python scripts/get_repo_structure.py --repo mayidan/project-test --branch dev

# 获取所有可见仓库的文件树
python scripts/get_repo_structure.py --all
```

## 参数说明
- `--repo`：指定仓库，格式为 `owner/reponame`
- `--branch`：指定分支，默认为主分支（main 或 master）
- `--all`：获取所有可见仓库的文件树

## 返回内容
每个仓库返回：
- 仓库名称、默认分支
- 完整文件树（路径、类型、大小）
- 文件分类结果（代码/文档/附件/配置）
- 目录层级结构