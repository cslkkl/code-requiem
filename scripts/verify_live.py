"""设置 GITHUB_TOKEN 并跑三个示例仓库 + 确定性验证。

用法：
    .venv\\Scripts\\python.exe -u scripts\\verify_live.py

TOKEN 来源优先级：环境变量 > gh auth token（本机已登录）。
不要把 token 写进任何文件。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if not os.environ.get("GITHUB_TOKEN"):
    try:
        token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
        os.environ["GITHUB_TOKEN"] = token
    except Exception as exc:
        print("⚠ 拿不到 GITHUB_TOKEN（gh 未登录？）：", exc)

REPOS = [
    "https://github.com/kornia/kornia",
    "https://github.com/cslkkl/dsh-cpa-switch",
    "https://github.com/facebookarchive/caffe2",
]


def main() -> int:
    from src.analyzer import analyze_repo

    results = {}
    for url in REPOS:
        t0 = time.time()
        m = analyze_repo(url)
        elapsed = time.time() - t0
        name = m["repo"].get("name") or url
        if not m.get("ok"):
            print(f"❌ {name}: {m['error']['kind']} — {m['error']['message']} ({elapsed:.1f}s)")
            results[name] = {"ok": False, "error": m["error"]}
            continue
        s = m["score"]
        a = m["authors"]
        print(
            f"✅ {name}: {s['total']} 分 / {s['mode']} / bus_factor={a['bus_factor']} "
            f"/ commits={m['life']['total_commits']} / 出生={m['life']['first_commit'][:10]} "
            f"/ {elapsed:.1f}s"
        )
        results[name] = {
            "ok": True,
            "score": s["total"],
            "mode": s["mode"],
            "bus_factor": a["bus_factor"],
            "fingerprint": m["deterministic_key"],
        }

    # 确定性验证：同一仓库连跑两次
    print("\n── 确定性验证 ──")
    a = analyze_repo(REPOS[0])
    b = analyze_repo(REPOS[0])
    same = (
        a.get("ok")
        and b.get("ok")
        and a["score"]["total"] == b["score"]["total"]
        and a["deterministic_key"] == b["deterministic_key"]
    )
    print(f"同一仓库两次运行分数相同：{same}")
    if a.get("ok") and b.get("ok"):
        print(
            f"  A={a['score']['total']} ({a['deterministic_key']})  B={b['score']['total']} ({b['deterministic_key']})"
        )

    out = ROOT / "docs" / "verification.json"
    out.write_text(
        json.dumps({"results": results, "deterministic": same}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n📄 验证结果已写入 {out}")
    return 0 if all(r.get("ok") for r in results.values()) and same else 1


if __name__ == "__main__":
    raise SystemExit(main())
