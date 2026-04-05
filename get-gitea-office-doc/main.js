import { execSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * 从 Gitea 下载并解析办公文档内容
 * @param {Object} params
 * @param {string} params.url - Gitea 文件 raw 下载地址（与 local 二选一）
 * @param {string} params.local - 本地文件路径（与 url 二选一）
 * @param {string} params.token - Gitea API Token（可选，优先从 .env 读取）
 * @param {string} params.filename - 手动指定文件名用于类型判断（可选）
 */
export async function run({ url = null, local = null, token = null, filename = null }) {
  if (!url && !local) {
    return "错误：请提供 url 或 local 参数。";
  }

  let cmd = `python scripts/extract_office_doc.py`;

  if (url) {
    cmd += ` --url "${url}"`;
    if (token) {
      cmd += ` --token "${token}"`;
    }
  } else {
    cmd += ` --local "${local}"`;
  }

  if (filename) {
    cmd += ` --filename "${filename}"`;
  }

  try {
    const output = execSync(cmd, {
      cwd: __dirname,
      encoding: "utf-8",
    });

    return output;
  } catch (error) {
    return `解析办公文档失败：${error.message}`;
  }
}