import { execSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * 获取 Gitea 仓库的提交记录
 * @param {Object} params
 * @param {string} params.mode - 查询模式：recent（按条数）或 timerange（按时间段）
 * @param {number} params.limit - 获取条数，mode=recent时使用，默认10
 * @param {number} params.hours - 获取多少小时内的提交，mode=timerange时使用，默认24
 * @param {string} params.repo - 指定仓库，格式：owner/reponame，不填则获取所有可见仓库
 */
export async function run({ mode = "recent", limit = 10, hours = 24, repo = null }) {
  // 拼接命令行参数
  let cmd = `python scripts/get_commits.py --mode ${mode} --limit ${limit} --hours ${hours}`;
  if (repo) {
    cmd += ` --repo ${repo}`;
  }

  try {
    const output = execSync(cmd, {
      cwd: __dirname,
      encoding: "utf-8",
    });

    const commits = JSON.parse(output);

    if (commits.length === 0) {
      return "没有找到符合条件的提交记录。";
    }

    // 格式化输出，方便Agent读取和总结
    let result = `共找到 ${commits.length} 条提交记录：\n\n`;
    for (const c of commits) {
      result += `📦 仓库：${c.repo}\n`;
      result += `👤 提交者：${c.author}\n`;
      result += `🕐 时间：${c.time}\n`;
      result += `💬 说明：${c.message}\n`;
      result += `📝 改动文件：\n`;
      for (const f of c.files) {
        result += `   - ${f.filename}（${f.status}，+${f.additions} -${f.deletions}）\n`;
        if (f.patch) {
          result += `     diff:\n${f.patch}\n`;
        }
      }
      result += `\n---\n\n`;
    }

    return result;
  } catch (error) {
    return `获取提交记录失败：${error.message}`;
  }
}