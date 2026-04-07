import { execSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * 生成 Gitea 各仓库进度报告并发送邮件给管理员。
 * 脚本只负责取数据，AI 分析和发邮件由 OpenClaw 根据 skill.md 指示完成。
 * @param {Object} params
 * @param {string} params.repo - 指定仓库，格式：owner/reponame，不填则处理所有可见仓库
 * @param {number} params.hours - 时间范围，24=过去24小时，168=过去7天，默认168
 */
export async function run({ repo = null, hours = 168 }) {
  let cmd = `python scripts/generate_report.py --hours ${hours}`;
  if (repo) {
    cmd += ` --repo ${repo}`;
  }

  try {
    const output = execSync(cmd, {
      cwd: __dirname,
      encoding: "utf-8",
      timeout: 120000
    });

    const results = JSON.parse(output);

    // 把结构化数据返回给 OpenClaw
    // OpenClaw 会根据 skill.md 的指示自己分析并调用 imap-smtp-email 发邮件
    let response = `已获取 ${results.length} 个仓库的提交数据，请根据以下数据依次为每个仓库生成报告并发送邮件：\n\n`;

    for (const r of results) {
      response += JSON.stringify(r, null, 2);
      response += "\n\n---\n\n";
    }

    return response;

  } catch (error) {
    return `生成报告失败：${error.message}`;
  }
}