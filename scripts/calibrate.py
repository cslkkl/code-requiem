"""标定脚本 —— 跑 50 个不同类别的真实仓库，采集分数与六维明细。

目的不是「训练模型」（本项目分析层零 AI），而是**用真实分布检验打分表是否合理**：

  1. 该活的仓库是否拿到高分？
  2. 该死的仓库是否拿到低分？
  3. 有没有某个维度几乎恒为满分或恒为零（说明这一维没有区分度，等于白送/白扣）？
  4. 有没有仓库被明显误判（例如维护良好的小项目被打成「已故」）？

结果逐仓库增量写入 JSON，中途失败也能保留已完成部分。

用法：
    $env:GITHUB_TOKEN = gh auth token
    .venv\\Scripts\\python.exe -u scripts\\calibrate.py
    .venv\\Scripts\\python.exe -u scripts\\calibrate.py --only 10   # 只跑前 10 个冒烟
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OUT = ROOT / "docs" / "calibration.json"

# 50 个仓库，按「人工预期」分类。
# expect 是人工判断的粗档位，用来和算法结果对照 —— 不是评分标准，
# 只是「如果算法给 alive 而 expect=dead，就值得看一眼」这种提示。
REPOS: list[tuple[str, str, str]] = [
    # ── 活跃的大型开源项目（预期 alive）──────────────────────────────
    ("kornia/kornia", "alive", "活跃视觉库"),
    ("psf/requests", "alive", "老牌 HTTP 库，仍在维护"),
    ("pallets/flask", "alive", "主流 Web 框架"),
    ("django/django", "alive", "大型 Web 框架"),
    ("numpy/numpy", "alive", "科学计算基础设施"),
    ("pandas-dev/pandas", "alive", "数据分析基础设施"),
    ("facebook/react", "alive", "前端框架"),
    ("vuejs/core", "alive", "前端框架"),
    ("rust-lang/rust", "alive", "编译器"),
    ("torvalds/linux", "alive", "巨型仓库，测采样"),
    # ── 活跃的中型项目（预期 alive）──────────────────────────────────
    ("tiangolo/fastapi", "alive", "现代 Web 框架"),
    ("streamlit/streamlit", "alive", "数据应用框架"),
    ("huggingface/transformers", "alive", "模型库"),
    ("openai/openai-python", "alive", "官方 SDK"),
    ("pydantic/pydantic", "alive", "数据校验库"),
    ("Textualize/rich", "alive", "终端渲染库"),
    ("encode/httpx", "alive", "HTTP 客户端"),
    ("psf/black", "alive", "代码格式化"),
    ("astral-sh/ruff", "alive", "极快的 linter"),
    ("pytest-dev/pytest", "alive", "测试框架"),
    # ── 单人 / 小团队维护（预期 critical 或 alive，关键看 bus factor）──
    ("cslkkl/code-requiem", "critical", "本项目，单人"),
    ("cslkkl/dsh-cpa-switch", "critical", "单人小项目"),
    ("kennethreitz/records", "critical", "单人项目，多年未大改"),
    ("sindresorhus/got", "alive", "单人主导的流行库"),
    ("tj/commander.js", "critical", "早期单人项目"),
    ("chalk/chalk", "critical", "单人主导的小库"),
    # ── 已死 / 归档（预期 dead）───────────────────────────────────────
    ("livid/v2ex-gae", "dead", "2017 停更"),
    ("facebookarchive/caffe2", "dead", "已归档"),
    ("facebookarchive/react-native-fbsdk", "dead", "已归档"),
    ("twbs/bootstrap-sass", "dead", "已停止维护"),
    ("kennethreitz/requests-html", "dead", "长期停滞"),
    ("moment/moment", "dead", "官方宣布停止开发"),
    ("gulpjs/gulp", "dead", "维护模式，多年无实质提交"),
    # ── Node / JS 生态（测 package.json 依赖解析）─────────────────────
    ("expressjs/express", "alive", "Node Web 框架"),
    ("axios/axios", "alive", "HTTP 客户端"),
    ("lodash/lodash", "alive", "工具库"),
    ("webpack/webpack", "alive", "打包器"),
    ("babel/babel", "alive", "编译器"),
    # ── 工具类 CLI（多为单人/小团队）──────────────────────────────────
    ("jgm/pandoc", "alive", "文档转换"),
    ("sharkdp/bat", "alive", "cat 替代"),
    ("BurntSushi/ripgrep", "alive", "grep 替代"),
    ("junegunn/fzf", "alive", "模糊查找"),
    ("neovim/neovim", "alive", "编辑器"),
    # ── 大型应用（测大仓库与 issue 量）────────────────────────────────
    ("home-assistant/core", "alive", "智能家居平台"),
    ("obsproject/obs-studio", "alive", "录屏软件"),
    ("electron/electron", "alive", "桌面容器"),
    ("microsoft/vscode", "alive", "编辑器"),
    ("godotengine/godot", "alive", "游戏引擎"),
    # ── 老牌但仍有维护（测「老仓库是否被误判」）───────────────────────
    ("jquery/jquery", "alive", "老牌库，仍在发版"),
    ("bootstrap/bootstrap", "alive", "老牌 CSS 框架"),
    ("vim/vim", "alive", "老牌编辑器"),
]


def _token() -> None:
    if os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
        return
    try:
        os.environ["GITHUB_TOKEN"] = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except Exception:
        print("⚠ 拿不到 GITHUB_TOKEN，匿名配额只有 60 次/小时，跑不完 50 个")


def _load() -> dict:
    if OUT.is_file():
        try:
            return json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(data: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, default=0, help="只跑前 N 个（冒烟用）")
    ap.add_argument("--force", action="store_true", help="重跑已完成的")
    ap.add_argument(
        "--per-repo-timeout",
        type=float,
        default=300.0,
        help="单个仓库的分析上限（秒）。超时记为 timeout 并继续下一个",
    )
    args = ap.parse_args()

    _token()
    from src import git_metrics as gm
    from src.analyzer import analyze_repo

    # 开跑前先清历史遗留目录。实测中进程被强杀会留下几十个浅克隆，
    # 其中一个巨型仓库能占几个 GB。
    stale = gm.cleanup_stale(max_age_hours=1.0)
    if stale:
        print(f"清理了 {stale} 个历史遗留临时目录")

    targets = REPOS[: args.only] if args.only else REPOS
    data = _load()
    print(f"目标 {len(targets)} 个仓库，已完成 {len(data)} 个\n")

    for i, (slug, expect, note) in enumerate(targets, 1):
        url = f"https://github.com/{slug}"
        if not args.force and slug in data and data[slug].get("ok"):
            print(f"[{i:02d}/{len(targets)}] {slug} 已有结果，跳过")
            continue

        # 单个仓库在独立线程里跑，超时就放弃它 —— 巨型仓库（如 torvalds/linux）
        # 能把整个批次拖死，不能让它毁掉后面几十个样本。
        box: dict = {}

        def _run(u=url, b=box):
            try:
                b["m"] = analyze_repo(u)
            except BaseException as exc:  # noqa: BLE001
                b["err"] = f"{type(exc).__name__}: {exc}"

        t0 = time.time()
        th = threading.Thread(target=_run, daemon=True)
        th.start()
        th.join(args.per_repo_timeout)
        elapsed = time.time() - t0

        if th.is_alive():
            print(f"[{i:02d}/{len(targets)}] TIMEOUT {slug:45} 超过 {args.per_repo_timeout:.0f}s，跳过")
            data[slug] = {
                "ok": False,
                "error": {"kind": "timeout", "message": f"超过 {args.per_repo_timeout:.0f} 秒"},
                "expect": expect,
                "note": note,
                "elapsed": round(elapsed, 1),
            }
            _save(data)
            continue

        if "err" in box:
            print(f"[{i:02d}/{len(targets)}] EXC {slug:45} {box['err']}")
            data[slug] = {
                "ok": False,
                "error": {"kind": "exception", "message": box["err"]},
                "expect": expect,
                "note": note,
                "elapsed": round(elapsed, 1),
            }
            _save(data)
            continue

        m = box.get("m") or {}

        if not m.get("ok"):
            kind = (m.get("error") or {}).get("kind")
            print(f"[{i:02d}/{len(targets)}] ✗ {slug:45} {kind} ({elapsed:.0f}s)")
            data[slug] = {
                "ok": False,
                "error": m.get("error"),
                "expect": expect,
                "note": note,
                "elapsed": round(elapsed, 1),
            }
            _save(data)
            continue

        parts = {k: v["score"] for k, v in (m["score"]["parts"] or {}).items()}
        rec = {
            "ok": True,
            "expect": expect,
            "note": note,
            "score": m["score"]["total"],
            "mode": m["score"]["mode"],
            "parts": parts,
            "bus_factor": m["authors"]["bus_factor"],
            "authors": m["authors"]["total"],
            "bot_commits": (m["life"] or {}).get("bot_commits", 0),
            "days_since_last": m["life"]["days_since_last_commit"],
            "commits": m["life"]["total_commits"],
            "loc": m["size"]["loc"],
            "files": m["size"]["tracked_files"],
            "lifespan": m["life"]["age_text"],
            "deps_status": m["deps"]["status"],
            "deps_oldest": m["deps"].get("oldest_pkg"),
            "deps_oldest_days": m["deps"].get("oldest_days"),
            "deps_scope": m["deps"].get("oldest_scope"),
            "deps_file": m["deps"].get("file"),
            "issues_status": m["issues"]["status"],
            "issues_median": m["issues"].get("median_days"),
            "issues_closed": m["issues"].get("closed_count"),
            "docs_chars": m["docs"]["readme_chars"],
            "has_readme": m["docs"]["has_readme"],
            "trend_recent": m["trend"]["recent_6m"],
            "trend_prev": m["trend"]["previous_6m"],
            "trend_insufficient": m["trend"]["insufficient_history"],
            "unavailable": (m.get("data_quality") or {}).get("unavailable_dimensions") or [],
            "todo": m["risks"]["todo_count"],
            "fixme": m["risks"]["fixme_count"],
            "sampled": m["sampled"],
            "elapsed": round(elapsed, 1),
        }
        data[slug] = rec
        _save(data)
        mismatch = "" if (rec["mode"] == expect) else f"  ⚠ 预期 {expect}"
        print(
            f"[{i:02d}/{len(targets)}] {slug:45} {rec['score']:3d} {rec['mode']:8} "
            f"bf={rec['bus_factor']:2d} 作者={rec['authors']:3d} "
            f"未提交={rec['days_since_last']:5d}d ({elapsed:.0f}s){mismatch}"
        )

    ok = [v for v in data.values() if v.get("ok")]
    print(f"\n完成 {len(ok)}/{len(targets)}，结果写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
