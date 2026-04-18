"""
main.py
Skill-H meeting_sync cron 入口。
每 30 分钟由 cron 调用一次。

完整流程：
1. 拉取腾讯会议未来 7 天列表（tencent_poller）
2. 汇总 Gitea 所有活跃会议（gitea_state）
3. 三向对比产生四类结果（diff_engine）
4. 处理新增 → 暂存 pending + 通知组织者
5. 处理取消 → status: cancelled + 全员通知
6. 处理改期 → 旧目录置 rescheduled + 新目录建立 + 全员通知
7. 归档清理 → cancelled/rescheduled 超 30 天的目录移入 archive
8. 写日志
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from scripts.tencent_poller import poll_tencent_meetings
from scripts.gitea_state    import collect_gitea_meetings, collect_archivable_meetings
from scripts.diff_engine    import diff_meetings
from scripts.handlers       import (
    handle_added, handle_cancelled,
    handle_rescheduled, handle_archive
)
from scripts.common         import write_log, now_beijing, AIFUSION_META_REPO


def main():
    start_time = now_beijing()
    print(f"[Skill-H] 开始同步 {start_time.isoformat()}")

    # ── Step 1: 拉取腾讯会议列表 ──────────────────────────
    tencent_meetings = []
    try:
        tencent_meetings = poll_tencent_meetings()
    except Exception as e:
        print(f"[Skill-H] 拉取腾讯会议失败（非致命，跳过本轮同步）: {e}")
        write_log("skill-h", AIFUSION_META_REPO, "",
                  "poll-tencent-failed", "error", {"error": str(e)})
        # 腾讯会议 API 失败时不执行取消/改期逻辑，避免误判
        _run_archive_only()
        return

    # ── Step 2: 汇总 Gitea 活跃会议 ──────────────────────
    gitea_meetings = []
    try:
        gitea_meetings = collect_gitea_meetings()
    except Exception as e:
        print(f"[Skill-H] 汇总 Gitea 会议失败: {e}")
        write_log("skill-h", AIFUSION_META_REPO, "",
                  "collect-gitea-failed", "error", {"error": str(e)})
        return

    # ── Step 3: 三向对比 ──────────────────────────────────
    diff = diff_meetings(tencent_meetings, gitea_meetings)

    added_count       = len(diff["added"])
    cancelled_count   = len(diff["cancelled"])
    rescheduled_count = len(diff["rescheduled"])

    # ── Step 4: 处理新增 ──────────────────────────────────
    for tm in diff["added"]:
        try:
            handle_added(tm)
        except Exception as e:
            mid = tm.get("meeting_id", "unknown")
            print(f"[Skill-H] handle_added 失败 {mid}: {e}")
            write_log("skill-h", AIFUSION_META_REPO, mid,
                      "handle-added-error", "error", {"error": str(e)})

    # ── Step 5: 处理取消 ──────────────────────────────────
    for gm in diff["cancelled"]:
        try:
            handle_cancelled(gm)
        except Exception as e:
            repo = gm.get("repo", "unknown")
            mdir = gm.get("meeting_dir", "unknown")
            print(f"[Skill-H] handle_cancelled 失败 {repo}/{mdir}: {e}")
            write_log("skill-h", repo, mdir,
                      "handle-cancelled-error", "error", {"error": str(e)})

    # ── Step 6: 处理改期 ──────────────────────────────────
    for gm, tm in diff["rescheduled"]:
        try:
            handle_rescheduled(gm, tm)
        except Exception as e:
            repo = gm.get("repo", "unknown")
            mdir = gm.get("meeting_dir", "unknown")
            print(f"[Skill-H] handle_rescheduled 失败 {repo}/{mdir}: {e}")
            write_log("skill-h", repo, mdir,
                      "handle-rescheduled-error", "error", {"error": str(e)})

    # ── Step 7: 归档清理 ──────────────────────────────────
    try:
        archivable = collect_archivable_meetings()
        handle_archive(archivable)
    except Exception as e:
        print(f"[Skill-H] 归档清理失败（非致命）: {e}")
        write_log("skill-h", AIFUSION_META_REPO, "",
                  "archive-error", "error", {"error": str(e)})

    # ── Step 8: 写汇总日志 ────────────────────────────────
    elapsed = (now_beijing() - start_time).total_seconds()
    write_log(
        "skill-h", AIFUSION_META_REPO, "",
        "sync-complete", "ok",
        {
            "tencent_count":    len(tencent_meetings),
            "gitea_count":      len(gitea_meetings),
            "added":            added_count,
            "cancelled":        cancelled_count,
            "rescheduled":      rescheduled_count,
            "elapsed_seconds":  round(elapsed, 1)
        }
    )
    print(
        f"[Skill-H] 同步完成，耗时 {elapsed:.1f}s | "
        f"新增={added_count} 取消={cancelled_count} 改期={rescheduled_count}"
    )


def _run_archive_only():
    """腾讯会议 API 失败时，仍执行归档清理（不依赖腾讯数据）。"""
    try:
        archivable = collect_archivable_meetings()
        handle_archive(archivable)
    except Exception as e:
        print(f"[Skill-H] archive-only 失败: {e}")


if __name__ == "__main__":
    main()