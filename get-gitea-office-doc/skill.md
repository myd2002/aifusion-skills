---
name: get-gitea-office-doc
version: 1.0.0
description: 从 Gitea 下载并解析办公文档（PDF/Word/PPT/Excel）的文字内容
author: mayidan
---

# Get Gitea Office Doc

## 功能描述
从 Gitea 仓库下载办公文档（.pdf / .docx / .pptx / .xlsx），用 Python 解析提取文字内容，返回给 Agent 进行总结分析。

## 使用场景
- 当 get-gitea-commits 发现 commit 里有办公文档，需要了解其具体内容时
- 当用户想查看/总结 Gitea 上某个 PPT、Word、PDF 的内容时
- 在生成提交报告时，需要深入了解办公文档变更内容时

## 使用方法
```bash
# 通过 Gitea 文件 URL 下载并提取
python scripts/extract_office_doc.py --url <gitea_raw_url> --token <api_token>

# 直接读取本地文件
python scripts/extract_office_doc.py --local <local_file_path>
```

## 参数说明
- `--url`：Gitea 文件的 raw 下载地址，格式：`http://43.156.243.152:3000/api/v1/repos/{owner}/{repo}/raw/{filepath}?ref={branch}`
- `--token`：Gitea API Token（从 .env 读取，通常不需要手动传）
- `--local`：本地文件路径（与 --url 二选一）
- `--filename`：手动指定文件名（用于类型判断，可选）

## 返回内容
- PDF：按页返回文字内容（最多20页）
- Word：返回标题层级 + 正文 + 表格
- PPT：按幻灯片返回所有文字（最多30页）
- Excel：按 Sheet 返回数据（每Sheet最多100行）

## 错误处理
- 扫描版 PDF（无文字层）：提示无法提取，建议 OCR
- 文件过大：自动截取前N页并提示
- 不支持的格式：返回支持格式列表
- 下载失败（401/403/404）：返回具体错误原因