"""健康分算法 —— 严格按 docs/SPEC.md 第 3 章实现。

本模块是**纯函数**集合：输入数字，输出数字。不访问网络、不读文件、不调用 LLM。
这样保证：
  1. 打分完全确定性（同一输入必然同一输出）；
  2. 可以用单元测试逐条验证 SPEC 的阈值表。
"""

from __future__ import annotations

# ── SPEC 3.1 最后提交距今（30 分）────────────────────────────────────────────
LAST_COMMIT_MAX = 30

# (上限天数含, 得分)
_LAST_COMMIT_TABLE = [
    (7, 30),
    (30, 25),
    (90, 18),
    (180, 10),
    (365, 5),
]


def score_last_commit(days: int) -> tuple[int, str]:
    """返回 (得分, 说明)。days 为距今天数。"""
    d = max(0, int(days))
    for limit, points in _LAST_COMMIT_TABLE:
        if d <= limit:
            return points, f"最后一次提交在 {d} 天前（≤{limit} 天档）"
    return 0, f"最后一次提交在 {d} 天前，超过 365 天"


# ── SPEC 3.2 Bus Factor（25 分）──────────────────────────────────────────────
BUS_FACTOR_MAX = 25
BUS_FACTOR_THRESHOLD = 0.60

_BUS_FACTOR_TABLE = {1: 0, 2: 10, 3: 17}


def bus_factor(commit_counts: dict[str, int]) -> int:
    """SPEC 3.2 原样实现：按提交数降序累加，累计占比首次 ≥60% 时数到的人头数。"""
    total = sum(commit_counts.values())
    if total == 0:
        return 0
    running = 0
    for i, n in enumerate(sorted(commit_counts.values(), reverse=True), 1):
        running += n
        if running / total >= BUS_FACTOR_THRESHOLD:
            return i
    return len(commit_counts)


def score_bus_factor(bf: int) -> tuple[int, str]:
    if bf <= 0:
        return 0, "无提交者信息"
    if bf == 1:
        return 0, "bus factor = 1：这个人离开，项目即终止"
    if bf in _BUS_FACTOR_TABLE:
        return _BUS_FACTOR_TABLE[bf], f"bus factor = {bf}，关键人风险偏高"
    return BUS_FACTOR_MAX, f"bus factor = {bf}，贡献分散，风险低"


# ── SPEC 3.3 提交活跃度趋势（15 分）──────────────────────────────────────────
TREND_MAX = 15
TREND_UNKNOWN = 10


def score_trend(recent_6m: int, previous_6m: int, insufficient_history: bool) -> tuple[int, str, float]:
    """返回 (得分, 说明, ratio)。"""
    if insufficient_history:
        return TREND_UNKNOWN, "仓库历史不足 12 个月，无法判断趋势，按 10 分计", 1.0
    ratio = recent_6m / max(previous_6m, 1)
    if ratio >= 1.0:
        return TREND_MAX, f"近 6 个月 {recent_6m} 次 vs 前 6 个月 {previous_6m} 次，活跃度持平或上升", ratio
    if ratio >= 0.5:
        return 10, f"近 6 个月 {recent_6m} 次 vs 前 6 个月 {previous_6m} 次，活跃度温和下滑", ratio
    if ratio >= 0.1:
        return 5, f"近 6 个月 {recent_6m} 次 vs 前 6 个月 {previous_6m} 次，活跃度明显衰退", ratio
    return 0, f"近 6 个月 {recent_6m} 次 vs 前 6 个月 {previous_6m} 次，基本停止提交", ratio


# ── SPEC 3.4 依赖健康度（15 分）──────────────────────────────────────────────
DEPS_MAX = 15
DEPS_NEUTRAL = 8  # 无依赖清单 / 网络失败


def score_deps(oldest_days: int | None, status: str) -> tuple[int, str]:
    """status: "ok" | "timeout" | "none" | "unsupported" | "skipped" """
    if status == "skipped":
        # 时间预算用尽时跳过查询：这是我们的取舍，不是项目的问题，按中性分
        return DEPS_NEUTRAL, "时间预算用尽，本次跳过依赖查询（按中性分 8 计）"
    if status in ("timeout", "unsupported", "error"):
        return DEPS_NEUTRAL, "依赖信息未获取（网络失败或超时），按中性分 8 计"
    if status == "none" or oldest_days is None:
        return DEPS_NEUTRAL, "无依赖清单，按中性分 8 计"
    d = int(oldest_days)
    if d < 180:
        return 15, f"最老依赖 {d} 天前发布（<180 天）"
    if d < 365:
        return 10, f"最老依赖 {d} 天前发布（180-364 天）"
    if d < 730:
        return 5, f"最老依赖 {d} 天前发布（365-729 天）"
    return 0, f"最老依赖 {d} 天前发布（≥730 天，或已从 registry 删除）"


# ── SPEC 3.5 Issue 响应（10 分）──────────────────────────────────────────────
ISSUES_MAX = 10


def score_issues(median_days: float | None, status: str) -> tuple[int, str]:
    if status != "ok" or median_days is None:
        return 1, "无 issue 记录或无法获取"
    d = float(median_days)
    if d < 7:
        return 10, f"issue 响应中位数 {d:.1f} 天（<7 天）"
    if d < 30:
        return 6, f"issue 响应中位数 {d:.1f} 天（7-29 天）"
    if d < 90:
        return 3, f"issue 响应中位数 {d:.1f} 天（30-89 天）"
    return 1, f"issue 响应中位数 {d:.1f} 天（≥90 天）"


# ── SPEC 3.6 文档完整度（5 分）───────────────────────────────────────────────
DOCS_MAX = 5


def score_docs(has_readme: bool, readme_chars: int, has_install: bool, has_example: bool) -> tuple[int, str]:
    if not has_readme:
        return 0, "无 README"
    if has_install and has_example:
        return 5, "README 含安装/使用说明与代码示例"
    if readme_chars >= 500:
        return 3, f"README 内容 {readme_chars} 字符，但缺少安装说明或代码示例"
    return 1, f"README 过短（{readme_chars} 字符）"


# ── SPEC 3.7 模式判定 ────────────────────────────────────────────────────────
ALIVE_MIN = 70
CRITICAL_MIN = 30


def decide_mode(total: int) -> str:
    if total >= ALIVE_MIN:
        return "alive"
    if total >= CRITICAL_MIN:
        return "critical"
    return "dead"


MODE_LABELS = {"alive": "存活", "critical": "承压", "dead": "已故"}
MODE_OUTPUTS = {
    "alive": "遗嘱模式",
    "critical": "临终关怀（遗嘱 + 悼词叠加）",
    "dead": "悼词模式",
}
