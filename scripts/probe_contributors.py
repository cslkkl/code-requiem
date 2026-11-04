"""验证：用 GitHub contributors API 算 bus factor 是否比 300 条提交样本更准。

背景：标定发现 pandas-dev/pandas 有 2000+ 贡献者却被判 bus_factor=1，
tiangolo/fastapi 被判「只有 5 位作者」——因为 API 路径只取了最近 300 条提交，
样本里恰好只有个位数作者，于是 bus factor 被严重低估。
"""

import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
try:
    from src import github_api  # noqa: F401
except ImportError:
    pass

H = {
    "User-Agent": "code-requiem/1.0",
    "Accept": "application/vnd.github+json",
    "Authorization": "Bearer " + (os.environ.get("GITHUB_TOKEN") or ""),
}

REPOS = [
    "pandas-dev/pandas",
    "tiangolo/fastapi",
    "pallets/flask",
    "rust-lang/rust",
    "psf/requests",
    "cslkkl/code-requiem",
    "livid/v2ex-gae",
]


def bus_factor_from_counts(counts):
    total = sum(counts)
    if total == 0:
        return 0
    running = 0
    for i, n in enumerate(sorted(counts, reverse=True), 1):
        running += n
        if running / total >= 0.60:
            return i
    return len(counts)


def contributors(slug, per_page=100, max_pages=5):
    """取 contributors 列表（含 contributions 计数）。"""
    out = []
    for page in range(1, max_pages + 1):
        try:
            r = requests.get(
                f"https://api.github.com/repos/{slug}/contributors",
                params={"per_page": per_page, "page": page, "anon": "false"},
                headers=H,
                timeout=20,
            )
        except Exception as exc:
            print(f"  {slug} 请求失败 {exc}")
            return out
        if r.status_code != 200:
            print(f"  {slug} HTTP {r.status_code}")
            return out
        data = r.json()
        if not isinstance(data, list) or not data:
            break
        out.extend(data)
        if len(data) < per_page:
            break
    return out


def main():
    print(f"{'repo':30} {'贡献者':>7} {'总提交':>8} {'bf':>4} {'首名占比':>9}")
    print("-" * 68)
    for slug in REPOS:
        rows = contributors(slug)
        if not rows:
            print(f"{slug:30} {'—':>7} {'—':>8} {'—':>4} {'—':>9}")
            continue
        counts = [int(x.get("contributions") or 0) for x in rows if isinstance(x, dict)]
        counts = [c for c in counts if c > 0]
        if not counts:
            continue
        total = sum(counts)
        bf = bus_factor_from_counts(counts)
        print(f"{slug:30} {len(counts):>7} {total:>8} {bf:>4} {max(counts) / total * 100:>8.1f}%")


if __name__ == "__main__":
    main()
