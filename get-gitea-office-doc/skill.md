---
name: get-gitea-office-doc
description: >
  Use this skill whenever you need to read the actual content of a PPT / Word / PDF (office document) file stored in Gitea.
  Triggers include: when get-gitea-commit or any other skill finds an office document in a commit/repo and needs to understand its content;
  when the user asks to summarize, analyze, or review a .pptx / .docx / .pdf / .xlsx file from Gitea;
  when a Gitea file URL or download URL points to an office document and you need its text.
  DO NOT skip this skill just because you already have the file URL — always use this skill to extract readable text from office documents.
  Workflow: download file from Gitea → extract text with Python → return content for Agent to summarize.
---

# get-gitea-office-doc Skill

## Purpose

Extract readable text content from PPT / Word / PDF office documents stored in Gitea, so Agent can summarize or analyze them.

**Supported formats:**
- `.pdf` — PDF documents
- `.docx` / `.doc` — Word documents
- `.pptx` / `.ppt` — PowerPoint presentations
- `.xlsx` / `.xls` — Excel spreadsheets (basic text/data extraction)

---

## Step 1: Get the Gitea Raw Download URL

You need the **raw download URL** for the file. It follows this pattern:

http://<gitea_host>/api/v1/repos/<owner>/<repo>/raw/<filepath>?ref=<branch>&token=<api_token>

**Gitea server info:**
- Host: `http://43.156.243.152:3000`

If you already have the file's raw URL from another skill (e.g., get-gitea-commit), use it directly.

If you only have the repo + path, construct the URL:
http://43.156.243.152:3000/api/v1/repos/{owner}/{repo}/raw/{filepath}?ref={branch}&token={token}

---

## Step 2: Download and Extract — Run the Python Script

Use the extraction script at `scripts/extract_office_doc.py`.
```bash
python3 scripts/extract_office_doc.py \
  --url "<raw_download_url>" \
  --token "<gitea_api_token>"
```

Or if you already have the file locally:
```bash
python3 scripts/extract_office_doc.py \
  --local "<local_file_path>"
```

The script will:
1. Download the file (if URL provided)
2. Detect file type by extension
3. Extract all text content
4. Print structured text to stdout

---

## Step 3: Combine with Other Content for Agent Summary

After extracting the text, combine it with whatever triggered this skill and produce a coherent summary.

**Example output structure:**

```
=== Commit Info ===
Commit: abc123 by ZhangYiwen on 2025-03-01
Message: "Add design spec for dexterous hand v2"

=== Office Document Content: design_spec_v2.pptx ===
[Slide 1] Title: Dexterous Hand Design Specification v2
[Slide 2] Overview: This document covers...
...

=== Summary Request ===
Please summarize the key points of the design document above.
```

---

## Error Handling

| Situation | Action |
|-----------|--------|
| File is scanned PDF (no text layer) | Report: "PDF appears to be a scanned image, text extraction not possible without OCR" |
| File too large (>50MB) | Extract first 20 pages / slides only, note truncation |
| Unsupported format | Report the file type and skip extraction |
| Download fails (401/403) | Check token; report authentication error |
| Download fails (404) | Check URL/branch; report file not found |

---

## Quick Reference: When Called from Another Skill

When `get-gitea-commit` or similar skill encounters an office document:

1. Note the file's raw URL or (repo + path + branch)
2. Call this skill immediately before returning results
3. Append extracted text to the commit/issue context
4. Then summarize everything together

**Do not** return a commit summary that says "contains a .pptx file" without first using this skill to extract its content.