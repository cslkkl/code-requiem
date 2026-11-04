"""代码安魂曲 · 分析引擎（Day 1 + Day 2）

唯一对外入口：
    analyze_repo(url, now=None, token=None) -> dict

设计约束（来自 docs/SPEC.md 第 2 章）：
    分析层必须完全确定性 —— 不调用 LLM、不使用随机数。
    同一个仓库在同一时刻分析两次，健康分必须完全相同。

命令行用法：
    .venv\\Scripts\\python.exe -u src\\analyzer.py https://github.com/kornia/kornia
    .venv\\Scripts\\python.exe -u src\\analyzer.py https://github.com/kornia/kornia --json out.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

# 允许 `python src/analyzer.py` 直接跑（把仓库根与 src 都加入 sys.path），
# 同时支持 `from src.analyzer import analyze_repo` 的包内导入。
_SRC_DIR = Path(__file__).resolve().parent
for _p in (_SRC_DIR, _SRC_DIR.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:  # 包内导入（Streamlit / HF Spaces / 测试）
    from . import deps as deps_mod  # type: ignore
    from . import (
        fetch_api,  # type: ignore
        github_api,  # type: ignore
        net,  # type: ignore
        scoring,  # type: ignore
    )
    from . import git_metrics as gm  # type: ignore
    from . import issues as issues_mod  # type: ignore
except ImportError:  # 脚本模式（python src/analyzer.py）
    import deps as deps_mod  # type: ignore  # noqa: E402
    import fetch_api  # type: ignore  # noqa: E402
    import git_metrics as gm  # type: ignore  # noqa: E402
    import github_api  # type: ignore  # noqa: E402
    import issues as issues_mod  # type: ignore  # noqa: E402
    import net  # type: ignore  # noqa: E402
    import scoring  # type: ignore  # noqa: E402

VERSION = "1.0.0"

# Windows 控制台默认 GBK，输出中文/符号会炸。强制切 UTF-8 并容错。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

# ── 错误类型：SPEC 第 5 章 7 种边界情况 ──────────────────────────────────────
ERROR_MESSAGES = {
    "not_found": "无法访问该仓库，请确认它是公开的，且地址没有拼错",
    "private": "无法访问该仓库，请确认它是公开的",
    "network": "网络异常，无法访问托管服务，请稍后重试",
    "empty": "这个项目还没有出生",
    "invalid": "这不是一个有效的 Git 仓库地址",
    "rate_limited": "GitHub API 配额已用完，暂时拿不到这个仓库的数据。配置 GITHUB_TOKEN 可把配额从 60 次/小时提升到 5000 次/小时",
    "unknown": "分析过程中出现未预期的问题",
}


def _err(kind: str, message: str | None = None) -> dict:
    return {"kind": kind, "message": message or ERROR_MESSAGES.get(kind, ERROR_MESSAGES["unknown"])}


# ── URL 解析 ────────────────────────────────────────────────────────────────
_GITHUB_RE = re.compile(r"github\.com[/:]([^/\s]+)/([^/\s#?]+)", re.I)
_GITLAB_RE = re.compile(r"gitlab\.com[/:]([^/\s]+)/([^/\s#?]+)", re.I)
_SSH_RE = re.compile(r"^git@([^:]+):(.+?)(?:\.git)?$")


def parse_repo_input(raw: str) -> dict:
    """把用户输入解析成结构化 repo 信息。支持 HTTPS / SSH / owner/repo 简写 / 本地路径。"""
    text = (raw or "").strip()
    info = {
        "input": raw or "",
        "host": "other",
        "owner": "",
        "name": "",
        "url": "",
        "is_local": False,
    }
    if not text:
        info["host"] = "invalid"
        return info

    # 本地路径
    p = Path(text)
    if p.exists() and p.is_dir():
        info.update({"host": "local", "is_local": True, "name": p.name, "url": str(p.resolve())})
        return info

    m = _GITHUB_RE.search(text)
    if m:
        name = m.group(2)
        if name.endswith(".git"):
            name = name[:-4]
        info.update({"host": "github", "owner": m.group(1), "name": name,
                     "url": f"https://github.com/{m.group(1)}/{name}.git"})
        return info

    m = _GITLAB_RE.search(text)
    if m:
        name = m.group(2)
        if name.endswith(".git"):
            name = name[:-4]
        info.update({"host": "gitlab", "owner": m.group(1), "name": name,
                     "url": f"https://gitlab.com/{m.group(1)}/{name}.git"})
        return info

    m = _SSH_RE.match(text)
    if m:
        host = m.group(1)
        path = m.group(2)
        name = path.split("/")[-1].removesuffix(".git")
        owner = path.split("/")[0] if "/" in path else ""
        h = "github" if "github" in host else ("gitlab" if "gitlab" in host else "other")
        info.update({"host": h, "owner": owner, "name": name, "url": text})
        return info

    # owner/repo 简写
    if re.fullmatch(r"[A-Za-z0-9._\-]+/[A-Za-z0-9._\-]+", text):
        owner, name = text.split("/")
        info.update({"host": "github", "owner": owner, "name": name,
                     "url": f"https://github.com/{owner}/{name}.git"})
        return info

    # 其它 https 地址
    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        parts = [x for x in parsed.path.split("/") if x]
        if len(parts) >= 2:
            name = parts[1].removesuffix(".git")
            h = "github" if "github" in parsed.netloc else ("gitlab" if "gitlab" in parsed.netloc else "other")
            info.update({"host": h, "owner": parts[0], "name": name, "url": text})
            return info
        info["host"] = "invalid"
        return info

    info["host"] = "invalid"
    return info


# ── 主流程 ──────────────────────────────────────────────────────────────────
def _empty_result(repo: dict, error: dict, elapsed: float = 0.0) -> dict:
    return {
        "ok": False,
        "error": error,
        "repo": repo,
        "sampled": False,
        "sample_note": None,
        "life": {
            "first_commit": "", "last_commit": "", "last_commit_msg": "",
            "total_commits": 0, "age_days": 0, "span_days": 0,
            "days_since_last_commit": 0, "age_text": "",
        },
        "size": {"tracked_files": 0, "loc": 0, "modules": 0, "top_dirs": [], "languages": []},
        "authors": {"total": 0, "bus_factor": 0, "top": []},
        "trend": {"recent_6m": 0, "previous_6m": 0, "ratio": 0.0, "insufficient_history": True},
        "deps": {"status": "none", "ecosystem": "none", "count": 0,
                 "oldest_pkg": None, "oldest_days": None, "items": []},
        "issues": {"status": "unavailable", "closed_count": 0, "open_count": 0, "median_days": None},
        "docs": {"has_readme": False, "readme_path": None, "readme_chars": 0,
                 "has_install": False, "has_usage": False, "has_example": False},
        "risks": {"todo_count": 0, "fixme_count": 0, "stale_core_files": [], "hotspots": []},
        "score": {"total": 0, "mode": "dead", "parts": {}},
        "elapsed_sec": round(elapsed, 2),
        "deterministic_key": "",
    }


def analyze_repo(url: str, *, now: datetime | None = None, token: str | None = None,
                 progress=None) -> dict:
    """分析任意公开 Git 仓库，返回 CONTRACT.md 第 2 节定义的结构。

    progress: 可选回调 progress(stage: str)，用于界面显示进度。
    """
    t0 = time.perf_counter()
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    def _p(stage: str):
        if progress:
            try:
                progress(stage)
            except Exception:
                pass

    repo = parse_repo_input(url)
    if repo["host"] == "invalid":
        return _empty_result(repo, _err("invalid"), time.perf_counter() - t0)

    workdir = None
    try:
        data_source = "git"
        repo_path = None

        if repo["is_local"]:
            repo_path = repo["url"]
            if not (Path(repo_path) / ".git").exists():
                return _empty_result(repo, _err("invalid", "该本地目录不是 Git 仓库"),
                                     time.perf_counter() - t0)
        else:
            # 先探测 github.com:443。本机实测：api.github.com 通、github.com 不通，
            # 盲目 clone 会白等 42 秒（2 次 × 21 秒）。探测失败就直接走 API 兜底。
            need_api_fallback = False
            if repo["host"] == "github" and not net.github_git_available():
                need_api_fallback = True
                _p("git 直连不可用，切换到 GitHub API 采集…")
            else:
                _p("正在浅克隆仓库…")
                workdir = gm.make_workdir()
                try:
                    repo_path = gm.clone_repo(repo["url"], workdir)
                except gm.CloneError as exc:
                    if repo["host"] == "github" and fetch_api.can_use_api():
                        _p("克隆失败，改用 GitHub API 采集…")
                        need_api_fallback = True
                    else:
                        return _empty_result(repo, _err(exc.kind, exc.message),
                                             time.perf_counter() - t0)

            if need_api_fallback:
                data_source = "github-api"
                if workdir is None:
                    workdir = gm.make_workdir()

        if data_source == "github-api":
            return _analyze_via_api(
                repo=repo, now=now, workdir=workdir, t0=t0, progress=_p,
            )

        if gm.is_empty_repo(repo_path):
            return _empty_result(repo, _err("empty"), time.perf_counter() - t0)

        # ── 采样判断（SPEC 5：>500MB 或 >50000 commit）──────────────────────
        sampled = False
        sample_note = None
        try:
            size_mb = gm.dir_size_mb(repo_path)
        except Exception:
            size_mb = 0.0

        _p("正在采集提交历史…")
        hist = gm.collect_commits(repo_path, now=now)
        if not hist["commits"]:
            return _empty_result(repo, _err("empty"), time.perf_counter() - t0)

        # ── 用 GitHub API 校正三项「浅克隆拿不到」的事实 ──────────────────
        # 1) 首次提交：--depth 200 的日志最早一条并不是仓库诞生时间，
        #    用仓库 created_at 代替（并保留 git 日志里的最早时间作为备选）。
        # 2) 总提交数：rev-list 在浅克隆里只数得到 200，用 API 的 Link 头拿精确值。
        # 3) 6/6 月趋势：深度 200 会让「前 6 个月」永远为 0，导致趋势恒判为增长。
        repo_meta = None
        if repo["host"] == "github" and fetch_api.can_use_api():
            _p("正在校正仓库元信息与趋势窗口…")
            repo_meta = github_api.repo_meta(repo["owner"], repo["name"])
            total = github_api.commit_count(repo["owner"], repo["name"])
            if total:
                hist["total_commits"] = total
            recent = github_api.commit_count(
                repo["owner"], repo["name"], since=now - timedelta(days=182)
            )
            prev = github_api.commit_count(
                repo["owner"], repo["name"],
                since=now - timedelta(days=364), until=now - timedelta(days=182),
            )
            if recent is not None and prev is not None:
                hist["recent_6m"] = recent
                hist["previous_6m"] = prev
            if repo_meta and repo_meta.get("created_at"):
                try:
                    birth = datetime.fromisoformat(repo_meta["created_at"].replace("Z", "+00:00"))
                    hist["first"] = birth
                    hist["insufficient_history"] = (now - birth).days < 365
                except Exception:
                    pass

        if size_mb > gm.MAX_REPO_MB or hist["total_commits"] > 50000:
            sampled = True
            sample_note = (f"仓库较大（{size_mb:.0f} MB / {hist['total_commits']} 次提交），"
                           f"已采样最近 {hist['analyzed_commits']} 个 commit 进行分析")
        elif hist.get("truncated"):
            sampled = True
            sample_note = f"已采样最近 {hist['analyzed_commits']} 个 commit（总提交 {hist['total_commits']}）"

        _p("正在统计代码规模与风险线索…")
        size = gm.count_loc(repo_path)
        docs_info = gm.analyze_readme(repo_path)
        risks_raw = gm.scan_risks(repo_path, size["code_files"])
        stale = gm.stale_core_files(repo_path, size["code_files"], now=now, limit=8)

        _p("正在查询依赖健康度…")
        deps_info = deps_mod.analyze_deps(repo_path)

        _p("正在查询 issue 响应…")
        # 本地仓库 / 非 GitHub：issue 项按不可用处理（SPEC 5 最后一条）
        if repo["host"] == "github":
            issues_info = issues_mod.analyze_issues(repo["owner"], repo["name"], host="github")
        else:
            issues_info = {"status": "not_github", "closed_count": 0, "open_count": 0,
                           "median_days": None}

        _p("正在计算健康分…")
        metrics = _build_metrics(
            repo=repo, now=now, hist=hist, size=size, docs_info=docs_info,
            risks_raw=risks_raw, stale=stale, deps_info=deps_info, issues_info=issues_info,
            sampled=sampled, sample_note=sample_note, repo_path=repo_path,
            elapsed=time.perf_counter() - t0,
        )
        return metrics

    except Exception as exc:  # 兜底：绝不让未捕获异常冒到界面
        trace = traceback.format_exc(limit=3)
        return _empty_result(
            repo,
            _err("unknown", f"{ERROR_MESSAGES['unknown']}：{type(exc).__name__}: {exc}".strip()),
            time.perf_counter() - t0,
        ) | {"trace": trace, "ok": False}
    finally:
        if workdir:
            gm.cleanup(workdir)


def _analyze_via_api(*, repo, now, workdir, t0, progress) -> dict:
    """GitHub API 兜底路径：tarball 取文件 + commits API 取历史。

    触发条件：github.com:443 不可达，或 git clone 失败但 api.github.com 通。
    """
    from datetime import timedelta

    owner, name = repo["owner"], repo["name"]
    progress("正在读取仓库元信息…")
    meta = fetch_api.repo_meta(owner, name)
    if meta is None:
        # 分清「仓库真不存在」和「配额用完」——这两件事对用户的意义完全不同
        if github_api.RATE_LIMITED or fetch_api.RATE_LIMITED:
            return _empty_result(
                repo,
                _err("rate_limited", github_api.LAST_ERROR or ERROR_MESSAGES["rate_limited"]),
                time.perf_counter() - t0,
            )
        return _empty_result(
            repo,
            _err("not_found", "无法访问该仓库，请确认它是公开的，且地址没有拼错"),
            time.perf_counter() - t0,
        )
    if meta.get("size_kb", 0) == 0 and not meta.get("pushed_at"):
        return _empty_result(repo, _err("empty"), time.perf_counter() - t0)

    progress("正在拉取提交历史（GitHub API）…")
    commits = fetch_api.fetch_commits(owner, name)
    if not commits:
        return _empty_result(repo, _err("empty"), time.perf_counter() - t0)
    commits.sort(key=lambda c: c["date"])

    progress("正在下载代码快照…")
    root = fetch_api.download_tarball(owner, name, workdir, branch=meta.get("default_branch"))
    if root is None:
        root = os.path.join(workdir, "repo")
        os.makedirs(root, exist_ok=True)

    # 用与 git 路径同构的方式统计规模与风险
    progress("正在统计代码规模与风险线索…")
    size = _scan_dir(root)
    docs_info = gm.analyze_readme(root)
    risks_raw = gm.scan_risks(root, size["code_files"])

    # 文件级「最后改动」：API 只能逐文件查，取前 8 个核心文件，各 1 个请求
    progress("正在补齐文件级历史…")
    stale: list[dict] = []
    for f in size["code_files"][:8]:
        got = fetch_api.file_last_commit_days(owner, name, f, now=now)
        if got is None:
            continue
        days, cnt = got
        try:
            loc = (Path(root) / f).read_bytes().count(b"\n")
        except Exception:
            loc = 0
        stale.append({"path": f, "days_untouched": days, "commits": cnt, "loc": loc})
    stale.sort(key=lambda x: (-x["commits"], -x["days_untouched"], x["path"]))

    progress("正在查询依赖健康度…")
    deps_info = deps_mod.analyze_deps(root)

    progress("正在查询 issue 响应…")
    issues_info = issues_mod.analyze_issues(owner, name, host="github")

    # 构造与 git 路径一致的 hist 结构
    cutoff_recent = now - timedelta(days=182)
    cutoff_prev = now - timedelta(days=364)
    author_counts: dict[str, int] = {}
    author_names: dict[str, str] = {}
    for c in commits:
        key = c["email"] or c["author"] or "unknown"
        author_counts[key] = author_counts.get(key, 0) + 1
        author_names.setdefault(key, c["author"])

    birth = None
    if meta.get("created_at"):
        try:
            from datetime import datetime as _dt
            birth = _dt.fromisoformat(meta["created_at"].replace("Z", "+00:00"))
        except Exception:
            birth = None

    hist = {
        "commits": commits,
        "total_commits": len(commits),
        "analyzed_commits": len(commits),
        "first": birth or commits[0]["date"],
        "last": commits[-1]["date"],
        "author_counts": author_counts,
        "author_names": author_names,
        "recent_6m": sum(1 for c in commits if cutoff_recent <= c["date"] <= now),
        "previous_6m": sum(1 for c in commits if cutoff_prev <= c["date"] < cutoff_recent),
        "insufficient_history": (now - (birth or commits[0]["date"])).days < 365,
        "truncated": len(commits) >= fetch_api.MAX_PAGES * fetch_api.PER_PAGE,
    }

    progress("正在计算健康分…")
    metrics = _build_metrics(
        repo=repo, now=now, hist=hist, size=size, docs_info=docs_info,
        risks_raw=risks_raw, stale=stale, deps_info=deps_info, issues_info=issues_info,
        sampled=True,
        sample_note=("当前网络无法直连 github.com，已改用 GitHub REST API + tarball 采集快照；"
                     f"提交历史取最近 {len(commits)} 条"),
        repo_path=root, elapsed=time.perf_counter() - t0,
    )
    metrics["data_source"] = "github-api"
    metrics["repo_meta"] = meta
    return metrics


def _scan_dir(root: str) -> dict:
    """不依赖 git 的目录扫描：文件数、代码行数、语言分布、顶层目录。"""
    tracked: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in gm.SKIP_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            tracked.append(rel)

    code_files: list[str] = []
    languages: dict[str, int] = {}
    top_dirs: dict[str, int] = {}
    for f in tracked:
        parts = f.split("/")
        if len(parts) > 1:
            top_dirs[parts[0]] = top_dirs.get(parts[0], 0) + 1
        ext = Path(f).suffix.lower()
        if ext in gm.CODE_EXTS:
            code_files.append(f)
            lang = gm.LANG_BY_EXT[ext]
            languages[lang] = languages.get(lang, 0) + 1

    loc = 0
    for f in code_files[:4000]:
        try:
            p = Path(root) / f
            if p.stat().st_size > 2 * 1024 * 1024:
                continue
            loc += p.read_bytes().count(b"\n")
        except Exception:
            continue

    return {
        "tracked_files": len(tracked),
        "loc": loc,
        "modules": len(code_files),
        "top_dirs": sorted([{"name": k, "files": v} for k, v in top_dirs.items()],
                           key=lambda x: (-x["files"], x["name"]))[:10],
        "languages": sorted([{"name": k, "files": v} for k, v in languages.items()],
                            key=lambda x: (-x["files"], x["name"]))[:6],
        "code_files": code_files,
    }


def _build_metrics(*, repo, now, hist, size, docs_info, risks_raw, stale,
                   deps_info, issues_info, sampled, sample_note, repo_path, elapsed) -> dict:
    """把采集到的原始数据组装成契约结构，并逐维打分。"""
    last = hist["last"]
    first = hist["first"]
    days_since = max(0, (now - last).days)
    age_days = max(0, (now - first).days)

    # ── 维度 1：最后提交距今 ───────────────────────────────────────────────
    s_last, d_last = scoring.score_last_commit(days_since)

    # ── 维度 2：bus factor ────────────────────────────────────────────────
    bf = scoring.bus_factor(hist["author_counts"])
    # SPEC 5：只有 1 个 commit 时，bus factor 强制为 1
    if len(hist["commits"]) <= 1:
        bf = 1 if hist["author_counts"] else 0
    s_bf, d_bf = scoring.score_bus_factor(bf)

    # ── 维度 3：趋势 ──────────────────────────────────────────────────────
    insufficient = hist["insufficient_history"] or len(hist["commits"]) <= 1
    s_trend, d_trend, ratio = scoring.score_trend(
        hist["recent_6m"], hist["previous_6m"], insufficient
    )

    # ── 维度 4：依赖 ──────────────────────────────────────────────────────
    s_deps, d_deps = scoring.score_deps(deps_info.get("oldest_days"), deps_info.get("status", "none"))

    # ── 维度 5：issue ─────────────────────────────────────────────────────
    s_iss, d_iss = scoring.score_issues(issues_info.get("median_days"), issues_info.get("status", "unavailable"))

    # ── 维度 6：文档 ──────────────────────────────────────────────────────
    s_doc, d_doc = scoring.score_docs(
        docs_info["has_readme"], docs_info["readme_chars"],
        docs_info["has_install"], docs_info["has_example"],
    )

    total = s_last + s_bf + s_trend + s_deps + s_iss + s_doc
    total = max(0, min(100, int(total)))
    mode = scoring.decide_mode(total)

    # ── 作者排行 ──────────────────────────────────────────────────────────
    total_commits = sum(hist["author_counts"].values()) or 1
    top_authors = []
    for email, cnt in sorted(hist["author_counts"].items(),
                             key=lambda kv: (-kv[1], kv[0]))[:8]:
        top_authors.append({
            "name": hist["author_names"].get(email, email),
            "email": email,
            "commits": cnt,
            "share": round(cnt / total_commits, 4),
        })

    # ── 风险点：stale 文件 + TODO/FIXME ───────────────────────────────────
    hotspots = risks_raw["hits"][:20]
    counts = risks_raw["counts"]

    # 若 stale 不足 3 个（小仓库），用体量最大的代码文件补齐，保证遗嘱第三章有料
    stale_final = list(stale)
    if len(stale_final) < 3:
        existing = {s["path"] for s in stale_final}
        for f in size["code_files"]:
            if len(stale_final) >= 3:
                break
            if f in existing:
                continue
            stale_final.append({"path": f, "days_untouched": -1, "commits": -1, "loc": 0})

    metrics = {
        "ok": True,
        "error": None,
        "repo": {k: repo[k] for k in ("input", "host", "owner", "name", "url", "is_local")},
        "sampled": sampled,
        "sample_note": sample_note,
        "life": {
            "first_commit": first.isoformat(),
            "last_commit": last.isoformat(),
            "last_commit_msg": hist["commits"][-1]["subject"] if hist["commits"] else "",
            "total_commits": hist["total_commits"],
            "analyzed_commits": hist["analyzed_commits"],
            "age_days": age_days,
            "span_days": age_days,
            "days_since_last_commit": days_since,
            "age_text": gm.human_age(age_days),
            "last_commit_age_text": gm.human_age(days_since),
        },
        "size": {
            "tracked_files": size["tracked_files"],
            "loc": size["loc"],
            "modules": size["modules"],
            "top_dirs": size["top_dirs"],
            "languages": size["languages"],
        },
        "authors": {
            "total": len(hist["author_counts"]),
            "bus_factor": bf,
            "top": top_authors,
        },
        "trend": {
            "recent_6m": hist["recent_6m"],
            "previous_6m": hist["previous_6m"],
            "ratio": round(ratio, 3),
            "insufficient_history": insufficient,
        },
        "deps": {
            "status": deps_info.get("status", "none"),
            "ecosystem": deps_info.get("ecosystem", "none"),
            "count": deps_info.get("count", 0),
            "oldest_pkg": deps_info.get("oldest_pkg"),
            "oldest_days": deps_info.get("oldest_days"),
            "items": deps_info.get("items", []),
            "file": deps_info.get("file", ""),
        },
        "issues": {
            "status": issues_info.get("status", "unavailable"),
            "closed_count": issues_info.get("closed_count", 0),
            "open_count": issues_info.get("open_count", 0),
            "median_days": issues_info.get("median_days"),
        },
        "docs": {
            "has_readme": docs_info["has_readme"],
            "readme_path": docs_info["readme_path"],
            "readme_chars": docs_info["readme_chars"],
            "has_install": docs_info["has_install"],
            "has_usage": docs_info["has_usage"],
            "has_example": docs_info["has_example"],
        },
        "risks": {
            "todo_count": counts.get("TODO", 0),
            "fixme_count": counts.get("FIXME", 0) + counts.get("HACK", 0) + counts.get("XXX", 0),
            "stale_core_files": stale_final,
            "hotspots": hotspots,
        },
        "score": {
            "total": total,
            "mode": mode,
            "parts": {
                "last_commit": {"score": s_last, "max": scoring.LAST_COMMIT_MAX,
                                "label": "最后提交距今", "detail": d_last},
                "bus_factor": {"score": s_bf, "max": scoring.BUS_FACTOR_MAX,
                               "label": "Bus Factor", "detail": d_bf},
                "trend": {"score": s_trend, "max": scoring.TREND_MAX,
                          "label": "提交活跃度趋势", "detail": d_trend},
                "deps": {"score": s_deps, "max": scoring.DEPS_MAX,
                         "label": "依赖健康度", "detail": d_deps},
                "issues": {"score": s_iss, "max": scoring.ISSUES_MAX,
                           "label": "Issue 响应", "detail": d_iss},
                "docs": {"score": s_doc, "max": scoring.DOCS_MAX,
                         "label": "文档完整度", "detail": d_doc},
            },
        },
        "elapsed_sec": round(elapsed, 2),
    }
    metrics["deterministic_key"] = _fingerprint(metrics)
    return metrics


def _fingerprint(m: dict) -> str:
    """确定性指纹：只由打分相关字段决定，便于验证「两次运行分数相同」。"""
    import hashlib
    basis = {
        "total": m["score"]["total"],
        "parts": {k: v["score"] for k, v in m["score"]["parts"].items()},
        "commits": m["life"]["total_commits"],
        "bus_factor": m["authors"]["bus_factor"],
        "days_since_last": m["life"]["days_since_last_commit"],
    }
    raw = json.dumps(basis, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


# ── 命令行 ──────────────────────────────────────────────────────────────────
def _print_report(m: dict) -> None:
    if not m.get("ok"):
        err = m.get("error") or {}
        print(f"\n❌ [{err.get('kind')}] {err.get('message')}")
        return

    repo = m["repo"]
    s = m["score"]
    print("=" * 68)
    print(f"  仓库      {repo['owner']}/{repo['name']}  ({repo['host']})")
    print(f"  健康分    {s['total']} / 100   → {scoring.MODE_LABELS[s['mode']]} · "
          f"{scoring.MODE_OUTPUTS[s['mode']]}")
    print("=" * 68)
    for key, part in s["parts"].items():
        bar_len = int(round(part["score"] / part["max"] * 20)) if part["max"] else 0
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"  {part['label']:<14} {bar} {part['score']:>2}/{part['max']:<2}  {part['detail']}")
    print("-" * 68)
    life = m["life"]
    print(f"  首次提交   {life['first_commit'][:10]}   最后提交 {life['last_commit'][:10]}"
          f"（{life['last_commit_age_text']}前）")
    print(f"  提交总数   {life['total_commits']}   分析范围 {life.get('analyzed_commits')}")
    print(f"  代码规模   {m['size']['tracked_files']} 文件 / {m['size']['loc']} 行 / "
          f"{m['size']['modules']} 个代码文件")
    print(f"  作者       {m['authors']['total']} 人   bus factor = {m['authors']['bus_factor']}")
    if m["authors"]["top"]:
        top = m["authors"]["top"][0]
        print(f"  头号作者   {top['name']} <{top['email']}>  {top['commits']} 次 "
              f"({top['share'] * 100:.1f}%)")
    tr = m["trend"]
    print(f"  趋势       近6月 {tr['recent_6m']} / 前6月 {tr['previous_6m']}  ratio={tr['ratio']}")
    dp = m["deps"]
    print(f"  依赖       {dp['status']} / {dp['ecosystem']} / {dp['count']} 个"
          + (f" / 最老 {dp['oldest_pkg']} {dp['oldest_days']} 天" if dp.get("oldest_pkg") else ""))
    iss = m["issues"]
    print(f"  Issues     {iss['status']} / 已关闭 {iss['closed_count']} / 中位 {iss['median_days']} 天")
    print(f"  文档       README={m['docs']['has_readme']} ({m['docs']['readme_chars']} 字符)")
    print(f"  风险       TODO {m['risks']['todo_count']} / FIXME+HACK {m['risks']['fixme_count']}")
    if m["sampled"]:
        print(f"  ⚠ 采样     {m['sample_note']}")
    print(f"  耗时       {m['elapsed_sec']}s   指纹 {m['deterministic_key']}")
    print("=" * 68)


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    out_json = None
    for a in argv[1:]:
        if a.startswith("--json="):
            out_json = a.split("=", 1)[1]

    if not args:
        print(__doc__)
        print("\n用法：python -u src/analyzer.py <仓库地址> [--json=out.json]")
        return 2

    url = args[0]
    print(f"⏳ 开始分析：{url}")
    m = analyze_repo(url)
    _print_report(m)
    if out_json:
        Path(out_json).write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"📄 完整指标已写入 {out_json}")
    return 0 if m.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
