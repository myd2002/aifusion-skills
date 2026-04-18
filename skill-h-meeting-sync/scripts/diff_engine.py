"""
diff_engine.py
三向对比腾讯会议列表与 Gitea 会议列表，产生四类结果。

对外暴露：
  diff_meetings(tencent_meetings, gitea_meetings) -> DiffResult

DiffResult:
{
  "added":      [tencent_item],          # 腾讯有 + Gitea 无
  "cancelled":  [gitea_item],            # Gitea 有 + 腾讯无
  "rescheduled":[(gitea_item, tencent_item)],  # 两边都有但时间不同
  "unchanged":  [(gitea_item, tencent_item)]   # 两边一致
}

匹配键：meeting_id
时间差阈值：超过 5 分钟视为改期
"""

import datetime

TIME_DIFF_THRESHOLD_SECONDS = 5 * 60   # 5 分钟


def diff_meetings(
    tencent_meetings: list[dict],
    gitea_meetings:   list[dict]
) -> dict:
    """
    三向对比，返回 DiffResult dict。

    tencent_meetings: tencent_poller.poll_tencent_meetings() 的输出
    gitea_meetings:   gitea_state.collect_gitea_meetings() 的输出
    """

    # 建立索引：meeting_id → item
    tencent_by_id: dict[str, dict] = {
        m["meeting_id"]: m
        for m in tencent_meetings
        if m.get("meeting_id")
    }
    gitea_by_id: dict[str, dict] = {
        g["meeting_id"]: g
        for g in gitea_meetings
        if g.get("meeting_id")
    }

    added      = []   # 腾讯有 + Gitea 无
    cancelled  = []   # Gitea 有 + 腾讯无
    rescheduled = []  # 两边都有但时间不同
    unchanged  = []   # 两边一致

    # ── 腾讯侧遍历 ───────────────────────────────────────
    for mid, tm in tencent_by_id.items():
        if mid not in gitea_by_id:
            # 腾讯有，Gitea 无 → 新增
            added.append(tm)
        else:
            gm = gitea_by_id[mid]
            if _is_rescheduled(tm, gm):
                rescheduled.append((gm, tm))
            else:
                unchanged.append((gm, tm))

    # ── Gitea 侧遍历 ─────────────────────────────────────
    for mid, gm in gitea_by_id.items():
        if mid not in tencent_by_id:
            # Gitea 有，腾讯无 → 取消
            cancelled.append(gm)

    print(
        f"[diff_engine] 对比结果: "
        f"新增={len(added)} 取消={len(cancelled)} "
        f"改期={len(rescheduled)} 一致={len(unchanged)}"
    )

    return {
        "added":       added,
        "cancelled":   cancelled,
        "rescheduled": rescheduled,
        "unchanged":   unchanged
    }


def _is_rescheduled(tencent_item: dict, gitea_item: dict) -> bool:
    """
    判断同一 meeting_id 的两条记录是否时间不同（超过阈值视为改期）。
    """
    t_ts = tencent_item.get("start_time", 0)
    g_ts = gitea_item.get("scheduled_ts", 0)

    if t_ts == 0 or g_ts == 0:
        return False

    return abs(t_ts - g_ts) > TIME_DIFF_THRESHOLD_SECONDS