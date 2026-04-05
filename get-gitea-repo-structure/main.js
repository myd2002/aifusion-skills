import { execSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * 获取 Gitea 仓库的完整文件树快照
 * @param {Object} params
 * @param {string} params.repo - 指定仓库，格式：owner/reponame，不填则获取所有可见仓库
 * @param {string} params.branch - 指定分支，默认为主分支
 * @param {boolean} params.all - 是否获取所有可见仓库
 */
export async function run({ repo = null, branch = null, all = false }) {
  let cmd = `python scripts/get_repo_structure.py`;
  if (all) {
    cmd += ` --all`;
  } else if (repo) {
    cmd += ` --repo ${repo}`;
  }
  if (branch) {
    cmd += ` --branch ${branch}`;
  }

  try {
    const output = execSync(cmd, {
      cwd: __dirname,
      encoding: "utf-8",
    });

    const results = JSON.parse(output);

    if (results.length === 0) {
      return "没有找到任何仓库。";
    }

    let result = "";
    for (const repo of results) {
      if (repo.error) {
        result += `❌ ${repo.error}\n\n`;
        continue;
      }

      result += `📦 仓库：${repo.repo}\n`;
      result += `🌿 分支：${repo.branch}（commit: ${repo.commit_sha}）\n`;
      result += `📁 文件总数：${repo.total_files}\n`;
      result += `📊 文件分类：\n`;
      for (const [category, count] of Object.entries(repo.category_summary)) {
        result += `   - ${category}：${count} 个\n`;
      }
      result += `🗂️ 顶层目录结构：${repo.top_level_structure.join("  /  ")}\n`;
      result += `\n📋 完整文件清单：\n`;
      for (const file of repo.files) {
        result += `   [${file.category}] ${file.path}（${file.size_bytes} bytes）\n`;
      }
      result += `\n---\n\n`;
    }

    return result;
  } catch (error) {
    return `获取仓库结构失败：${error.message}`;
  }
}