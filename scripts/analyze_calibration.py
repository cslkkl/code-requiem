"""标定结果分析 —— 从 49 个真实仓库的分布反推打分表的不妥之处。

看四件事：
  1. 模式误判：expect 与 mode 不一致的仓库，逐个人工复核根因
  2. 维度区分度：有没有哪一维几乎恒为满分或恒为零（等于白送/白扣）
  3. bus factor 分布：是否出现「大项目被判单人」这类方向性错误
  4. 分数分布：是否过度集中在某一段
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DIMS = ["last_commit", "bus_factor", "trend", "deps", "issues", "docs"]
MAX = {"last_commit": 30, "bus_factor": 25, "trend": 15, "deps": 15, "issues": 10, "docs": 5}


def main() -> int:
    data = json.loads(
        (Path(__file__).resolve().parent.parent / "docs" / "calibration.json").read_text(encoding="utf-8")
    )
    ok = {k: v for k, v in data.items() if v.get("ok")}
    fail = {k: v for k, v in data.items() if not v.get("ok")}

    print(f"样本：成功 {len(ok)} / 失败 {len(fail)}（总 {len(data)}）")
    if fail:
        desc = ", ".join(f"{k}({(val.get('error') or {}).get('kind')})" for k, val in fail.items())
        print("失败清单：", desc)
    print()

    # ── 1. 模式误判 ────────────────────────────────────────────────────
    print("=" * 78)
    print("① 模式误判（expect 与算法结果不一致）")
    print("=" * 78)
    mism = [(k, v) for k, v in ok.items() if v["mode"] != v["expect"]]
    for k, v in mism:
        p = v["parts"]
        zeros = [d for d in DIMS if p.get(d, 0) == 0]
        print(
            f"  {k:34} {v['score']:3d} {v['mode']:8} (预期 {v['expect']:8}) "
            f"bf={v['bus_factor']:2d} 未提交={v['days_since_last']:5d}d  零分维度={zeros or '-'}"
        )
    print(f"\n  误判 {len(mism)}/{len(ok)} = {len(mism) / len(ok) * 100:.0f}%")
    print()

    # ── 2. 维度区分度 ──────────────────────────────────────────────────
    print("=" * 78)
    print("② 维度区分度（满分率 / 零分率）")
    print("=" * 78)
    print(f"  {'维度':<14} {'满分':>6} {'零分':>6} {'均值':>7} {'标准差':>7}  判断")
    for d in DIMS:
        vals = [v["parts"].get(d, 0) for v in ok.values()]
        n = len(vals)
        full = sum(1 for x in vals if x == MAX[d])
        zero = sum(1 for x in vals if x == 0)
        mean = sum(vals) / n
        var = sum((x - mean) ** 2 for x in vals) / n
        sd = var**0.5
        verdict = ""
        if full / n > 0.6:
            verdict = "满分率过高，区分度差"
        if zero / n > 0.5:
            verdict = "零分率过高，区分度差"
        if sd < 1.5:
            verdict = verdict or "方差过小"
        print(f"  {d:<14} {full:>3}/{n:<3} {zero:>3}/{n:<3} {mean:>6.1f} {sd:>7.2f}  {verdict}")
    print()

    # ── 3. bus factor 分布 ─────────────────────────────────────────────
    print("=" * 78)
    print("③ bus factor 分布（应随项目规模递增，而不是随机）")
    print("=" * 78)
    bf_hist = Counter(v["bus_factor"] for v in ok.values())
    print("  bf 分布：", dict(sorted(bf_hist.items())))
    bf1_big = [k for k, v in ok.items() if v["authors"] >= 200 and v["bus_factor"] == 1]
    print(f"  贡献者 ≥200 人却判 bf=1 的仓库：{len(bf1_big)} 个")
    for k in bf1_big[:8]:
        v = ok[k]
        print(f"    {k:34} 作者={v['authors']:4d} bf=1")
    print()

    # ── 4. 分数分布 ────────────────────────────────────────────────────
    print("=" * 78)
    print("④ 分数分布")
    print("=" * 78)
    buckets = defaultdict(int)
    for v in ok.values():
        s = v["score"]
        buckets[(s // 10) * 10] += 1
    for b in sorted(buckets):
        bar = "#" * buckets[b]
        print(f"  {b:>3}-{b + 9:<3} {bar} {buckets[b]}")
    scores = sorted(v["score"] for v in ok.values())
    print(f"  中位数 {scores[len(scores) // 2]}  最小 {scores[0]}  最大 {scores[-1]}")
    print()

    # ── 5. 高分但已死的仓库（最危险的一类误判）──────────────────────────
    print("=" * 78)
    print("⑤ 最危险的误判：死仓库拿到高分 / 活仓库拿到低分")
    print("=" * 78)
    dead_high = [(k, v) for k, v in ok.items() if v["days_since_last"] > 365 and v["score"] >= 40]
    for k, v in sorted(dead_high, key=lambda x: -x[1]["score"]):
        print(
            f"  [死但高分] {k:32} score={v['score']:3d} 未提交={v['days_since_last']}d "
            f"parts={ {d: v['parts'].get(d) for d in DIMS} }"
        )
    alive_low = [(k, v) for k, v in ok.items() if v["days_since_last"] <= 30 and v["score"] < 60]
    for k, v in sorted(alive_low, key=lambda x: x[1]["score"]):
        print(
            f"  [活但低分] {k:32} score={v['score']:3d} 未提交={v['days_since_last']}d "
            f"parts={ {d: v['parts'].get(d) for d in DIMS} }"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
