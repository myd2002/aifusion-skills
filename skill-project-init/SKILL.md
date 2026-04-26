---
name: project-init
description: 自动初始化 Gitea 科研项目仓库。通过对话收集仓库名称、描述、成员、可见性等信息，自动创建仓库并写入标准目录结构（meeting/ docs/ src/ data/ reports/）、README.md、CONTRIBUTING.md，同时创建启动期 Issue 并发送邮件通知成员。支持对已有空仓库执行初始化（幂等保护）。
metadata:
  openclaw:
    emoji: "🚀"
    requires:
      bins:
        - node
        - python3
---

# 科研项目仓库初始化工具

通过与用户对话，自动完成 Gitea 仓库的标准化初始化。

## 使用方式

用户说"帮我建一个新仓库"或"帮我初始化 xxx 仓库"时触发。

Skill 会依次询问：
1. 仓库名称（英文，用于 URL）
2. 项目描述
3. 参与成员的 Gitea 用户名（选填）
4. 是否私有（默认公开）

收集完毕后自动执行初始化。

## 初始化内容

- 标准目录：`meeting/` `docs/` `src/` `data/` `reports/`
- 基础文档：`README.md`（含变量替换）、`CONTRIBUTING.md`（固定规范）
- 初始 Issue：全体通知 + 分工确认
- 邮件通知：发送给所有提供的成员

## 幂等保护

若仓库中已存在 `README.md`，则跳过初始化并提示用户。

## 环境变量

参考 `env-example.txt`，配置于 `~/.config/project-init/.env`。
