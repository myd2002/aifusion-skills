#!/usr/bin/env python3
import argparse
import os
import sys
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
GITEA_TOKEN = os.getenv("GITEA_TOKEN")


def ensure_deps():
    import subprocess
    packages = {
        "pypdf": "pypdf",
        "docx": "python-docx",
        "pptx": "python-pptx",
        "openpyxl": "openpyxl",
    }
    for module, pkg in packages.items():
        try:
            __import__(module)
        except ImportError:
            print(f"[Installing {pkg}...]", file=sys.stderr)
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pkg,
                 "--break-system-packages", "-q"]
            )

ensure_deps()

from pypdf import PdfReader
import docx as python_docx
from pptx import Presentation
import openpyxl


def download_file(url: str, token: str = None):
    req = urllib.request.Request(url)
    t = token or GITEA_TOKEN
    if t:
        req.add_header("Authorization", f"token {t}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            filename = url.split("?")[0].split("/")[-1]
            return content, filename
    except urllib.error.HTTPError as e:
        codes = {401: "认证失败，请检查 Token", 403: "无权限访问", 404: "文件不存在，请检查路径和分支"}
        print(f"ERROR: {codes.get(e.code, f'HTTP {e.code}')}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: 网络错误 - {e.reason}", file=sys.stderr)
        sys.exit(1)


def extract_pdf(filepath):
    try:
        reader = PdfReader(filepath)
        total = len(reader.pages)
        max_p = min(total, 20)
        lines = [f"[PDF：共 {total} 页" + (f"，显示前 {max_p} 页" if total > max_p else "") + "]"]
        for i, page in enumerate(reader.pages[:max_p]):
            text = page.extract_text()
            if text and text.strip():
                lines.append(f"\n--- 第 {i+1} 页 ---")
                lines.append(text.strip())
        result = "\n".join(lines)
        if len(result.replace(lines[0], "").strip()) < 50:
            return "[PDF 警告] 提取文字很少，该 PDF 可能是扫描件，需要 OCR 才能读取内容。\n" + result
        return result
    except Exception as e:
        return f"[PDF 错误] {e}"


def extract_docx(filepath):
    try:
        doc = python_docx.Document(filepath)
        lines = []
        cp = doc.core_properties
        if cp.title:
            lines.append(f"[文档标题：{cp.title}]")
        if cp.author:
            lines.append(f"[作者：{cp.author}]")
        lines.append("")
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            if para.style.name.startswith("Heading"):
                level = para.style.name.replace("Heading ", "").strip()
                lines.append(f"\n{'#' * int(level) if level.isdigit() else '#'} {text}")
            else:
                lines.append(text)
        if doc.tables:
            lines.append("\n--- 表格 ---")
            for idx, table in enumerate(doc.tables):
                lines.append(f"\n[表格 {idx+1}]")
                for row in table.rows:
                    lines.append(" | ".join(cell.text.strip() for cell in row.cells))
        return "\n".join(lines)
    except Exception as e:
        return f"[Word 错误] {e}"


def extract_pptx(filepath):
    try:
        prs = Presentation(filepath)
        total = len(prs.slides)
        max_s = min(total, 30)
        lines = [f"[PPT：共 {total} 张幻灯片" + (f"，显示前 {max_s} 张" if total > max_s else "") + "]"]
        for i in range(max_s):
            slide = prs.slides[i]
            texts = []
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        texts.append(text)
            if texts:
                lines.append(f"\n[第 {i+1} 张]")
                lines.extend(texts)
        return "\n".join(lines)
    except Exception as e:
        return f"[PPT 错误] {e}"


def extract_xlsx(filepath):
    try:
        wb = openpyxl.load_workbook(filepath, data_only=True, read_only=True)
        lines = [f"[Excel：共 {len(wb.sheetnames)} 个Sheet：{', '.join(wb.sheetnames)}]"]
        for name in wb.sheetnames:
            ws = wb[name]
            lines.append(f"\n--- Sheet：{name} ---")
            count = 0
            for row in ws.iter_rows(values_only=True):
                if all(c is None or str(c).strip() == "" for c in row):
                    continue
                lines.append(" | ".join(str(c) if c is not None else "" for c in row))
                count += 1
                if count >= 100:
                    lines.append("...（已截断，仅显示前100行）")
                    break
        return "\n".join(lines)
    except Exception as e:
        return f"[Excel 错误] {e}"


EXTRACTORS = {
    ".pdf": extract_pdf,
    ".docx": extract_docx,
    ".doc": extract_docx,
    ".pptx": extract_pptx,
    ".ppt": extract_pptx,
    ".xlsx": extract_xlsx,
    ".xls": extract_xlsx,
}


def extract(filepath, filename=None):
    ext = Path(filename or filepath).suffix.lower()
    if ext not in EXTRACTORS:
        return f"[不支持的格式] '{ext}'，支持：{', '.join(EXTRACTORS.keys())}"
    return EXTRACTORS[ext](filepath)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url")
    group.add_argument("--local")
    parser.add_argument("--token")
    parser.add_argument("--filename")
    args = parser.parse_args()

    if args.url:
        content, filename = download_file(args.url, args.token)
        override = args.filename or filename
        ext = Path(override).suffix.lower()
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            print(extract(tmp_path, override))
        finally:
            os.unlink(tmp_path)
    else:
        if not os.path.exists(args.local):
            print(f"ERROR: 文件不存在：{args.local}", file=sys.stderr)
            sys.exit(1)
        print(extract(args.local, args.filename or args.local))


if __name__ == "__main__":
    main()