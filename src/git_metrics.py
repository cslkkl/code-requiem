"""Git 指标采集 —— 克隆 + 读取提交历史 + 提取风险线索。

设计原则：
  * 只读，不修改被分析仓库；
  * 所有网络/磁盘失败都转成结构化错误，不抛出到上层；
  * 采集结果只依赖仓库内容本身（外加调用时传入的 now），因此可复现。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# 克隆深度：SPEC 要求 --depth 200
CLONE_DEPTH = 200
# SPEC 5：commit > 50000 或仓库 > 500MB 时只分析最近 2000 个 commit
MAX_COMMITS_ANALYZED = 2000
MAX_REPO_MB = 500

# 需要跳过统计的目录（第三方/vendor/构建产物）
SKIP_DIRS = {
    ".git",
    "node_modules",
    "vendor",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "target",
    ".idea",
    ".gradle",
    "site-packages",
    ".next",
    "coverage",
    ".tox",
    "Pods",
    "third_party",
}

# 常见语言扩展名
LANG_BY_EXT = {
    ".py": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C/C++",
    ".hpp": "C/C++",
    ".cc": "C/C++",
    ".cpp": "C/C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".m": "Objective-C",
    ".scala": "Scala",
    ".sh": "Shell",
    ".lua": "Lua",
    ".dart": "Dart",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".r": "R",
    ".sql": "SQL",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".hs": "Haskell",
}

# 视为「代码」的扩展名（用于 LOC 与模块统计）
CODE_EXTS = set(LANG_BY_EXT)

README_NAMES = (
    "README.md",
    "README.rst",
    "README.txt",
    "README",
    "readme.md",
    "Readme.md",
    "README.markdown",
    "README.MD",
)

TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
HOTSPOT_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b[:\s]*(.{0,120})")


class CloneError(Exception):
    """克隆失败，带结构化 kind。"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _git_env() -> dict:
    """构造一个干净的 git 环境变量。

    这台机器的 ~/.gitconfig 里有一条指向未启动 socks5 代理的配置，
    会让所有 github 操作超时。这里显式清掉，并禁用交互式凭据提示，
    保证克隆不会挂住。
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    # 覆盖全局配置里的坏代理（空值 = 不使用代理）
    env["GIT_CONFIG_COUNT"] = "2"
    env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.proxy"
    env["GIT_CONFIG_VALUE_0"] = ""
    env["GIT_CONFIG_KEY_1"] = "http.proxy"
    env["GIT_CONFIG_VALUE_1"] = ""
    return env


def _run_git(args: list[str], cwd: str | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_git_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def clone_repo(
    url: str, workdir: str, *, depth: int = CLONE_DEPTH, timeout: int = 180, attempts: int = 2
) -> str:
    """浅克隆到 workdir/repo。失败抛 CloneError，带 kind。

    kind 取值：not_found / private / network / empty / invalid

    重试策略：网络类失败重试 attempts 次（间隔 2s / 4s）；认证/不存在类失败立即返回，
    重试没有意义还会白白拖长时间。
    """
    dest = os.path.join(workdir, "repo")
    last: CloneError | None = None

    for i in range(attempts):
        args = ["clone", "--depth", str(depth), "--single-branch", "--no-tags", url, dest]
        try:
            proc = _run_git(args, timeout=timeout)
        except subprocess.TimeoutExpired:
            last = CloneError("network", "克隆超时（180 秒），可能是网络问题或仓库过大")
        except FileNotFoundError:
            raise CloneError("invalid", "系统未安装 git 命令") from None
        else:
            if proc.returncode == 0:
                return dest

            stderr = (proc.stderr or "") + (proc.stdout or "")
            low = stderr.lower()
            if (
                "authentication failed" in low
                or "could not read username" in low
                or "permission denied" in low
            ):
                raise CloneError("private", "无法访问该仓库，请确认它是公开的")
            if "not found" in low or "404" in low or "does not exist" in low:
                raise CloneError("not_found", "无法访问该仓库，请确认它是公开的，且地址没有拼错")
            if "empty repository" in low or "you appear to have cloned an empty" in low:
                raise CloneError("empty", "这个项目还没有出生")

            hints_network = (
                "could not resolve host" in low
                or "connection" in low
                or "timed out" in low
                or "failed to connect" in low
                or "couldn't connect" in low
                or "unable to access" in low
            )
            tail = stderr.strip().splitlines()[-1] if stderr.strip() else "未知错误"
            last = CloneError("network" if hints_network else "network", f"克隆失败：{tail}")

        if i < attempts - 1:
            time.sleep(2 * (i + 1))

    raise last or CloneError("network", "克隆失败（未知原因）")


def is_empty_repo(repo_path: str) -> bool:
    try:
        proc = _run_git(["rev-list", "--count", "--all"], cwd=repo_path, timeout=60)
        if proc.returncode != 0:
            return True
        return int(proc.stdout.strip() or "0") == 0
    except Exception:
        return False


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def human_age(days: int) -> str:
    """把天数转成中文「X 年 Y 个月」。"""
    days = max(0, int(days))
    if days < 30:
        return f"{days} 天"
    months = days // 30
    if months < 12:
        return f"{months} 个月"
    years, rem_months = divmod(months, 12)
    if rem_months == 0:
        return f"{years} 年"
    return f"{years} 年 {rem_months} 个月"


# ── 机器人账号识别 ──────────────────────────────────────────────────────────
# SPEC 3.2 只说「按 commit author email 去重」，没说机器人算不算人。
# 但 bus factor 的业务含义是「需要走掉几个**人**，项目就没人管了」——
# 机器人不会离职，把它算进分母会虚高 bus factor、掩盖真实单点风险。
# 所以这里排除机器人（更贴合 bus factor 的本意，属于对 SPEC 的合理细化）。
_BOT_EMAIL_RE = re.compile(
    r"(bot@|@bots?\.|\[bot\]|^(bot|dependabot|renovate|github-actions|actions|"
    r"pre-commit-ci|mergify|semantic-release|greenkeeper|snyk-bot|imgbot))",
    re.IGNORECASE,
)
_BOT_NAME_RE = re.compile(
    r"(bot$|\[bot\]|dependabot|renovate|github-actions|actions-user|pre-commit-ci|"
    r"mergify|semantic-release|snyk|imgbot|codecov)",
    re.IGNORECASE,
)


def is_bot(name: str, email: str) -> bool:
    """判断一个提交者是否机器人账号。"""
    em = (email or "").strip().lower()
    nm = (name or "").strip()
    if not em and not nm:
        return False
    if "[bot]" in em or em.endswith("@users.noreply.github.com") and "[bot]" in nm.lower():
        return True
    if _BOT_EMAIL_RE.search(em):
        return True
    return bool(_BOT_NAME_RE.search(nm))


def collect_commits(repo_path: str, *, now: datetime, max_commits: int = MAX_COMMITS_ANALYZED) -> dict:
    """从 git log 采集提交层面的全部指标。"""
    # 用 NUL 分隔字段、RS 分隔记录，避免提交信息里的特殊字符破坏解析
    fmt = "%H%x1f%an%x1f%ae%x1f%cI%x1f%s%x1e"
    proc = _run_git(
        ["log", f"--max-count={max_commits}", f"--pretty=format:{fmt}", "--no-merges"],
        cwd=repo_path,
        timeout=120,
    )
    raw = proc.stdout or ""
    commits: list[dict] = []
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n\r ")
        if not rec:
            continue
        parts = rec.split("\x1f")
        if len(parts) < 5:
            continue
        sha, an, ae, ciso, subj = parts[0], parts[1], parts[2], parts[3], parts[4]
        try:
            cdate = _as_utc(datetime.fromisoformat(ciso))
        except Exception:
            continue
        commits.append(
            {
                "sha": sha,
                "author": an,
                "email": (ae or "").strip().lower(),
                "date": cdate,
                "subject": subj,
            }
        )

    total_in_log = len(commits)
    if not commits:
        return {
            "commits": [],
            "total_commits": 0,
            "first": None,
            "last": None,
            "author_counts": {},
            "author_names": {},
            "recent_6m": 0,
            "previous_6m": 0,
            "insufficient_history": True,
            "truncated": False,
        }

    commits.sort(key=lambda c: c["date"])
    first, last = commits[0]["date"], commits[-1]["date"]

    author_counts: dict[str, int] = {}
    author_names: dict[str, str] = {}
    bot_counts: dict[str, int] = {}
    bot_commits = 0
    for c in commits:
        key = c["email"] or c["author"] or "unknown"
        if is_bot(c["author"], c["email"]):
            bot_counts[key] = bot_counts.get(key, 0) + 1
            bot_commits += 1
            continue
        author_counts[key] = author_counts.get(key, 0) + 1
        # 记录该 email 最近一次使用的 name
        author_names.setdefault(key, c["author"])

    # 趋势：最近 6 个月 vs 之前 6 个月
    cutoff_recent = now - timedelta(days=182)
    cutoff_prev = now - timedelta(days=364)
    recent_6m = sum(1 for c in commits if cutoff_recent <= c["date"] <= now)
    previous_6m = sum(1 for c in commits if cutoff_prev <= c["date"] < cutoff_recent)

    span_days = max(0, (now - first).days)
    insufficient = span_days < 365

    # 真实总提交数（浅克隆只有 depth 个，用 rev-list 数）
    real_total = total_in_log
    try:
        p = _run_git(["rev-list", "--count", "HEAD"], cwd=repo_path, timeout=60)
        if p.returncode == 0 and p.stdout.strip().isdigit():
            real_total = int(p.stdout.strip())
    except Exception:
        pass

    return {
        "commits": commits,
        "total_commits": real_total,
        "analyzed_commits": total_in_log,
        "first": first,
        "last": last,
        "author_counts": author_counts,
        "author_names": author_names,
        "bot_commits": bot_commits,
        "bot_accounts": len(bot_counts),
        "recent_6m": recent_6m,
        "previous_6m": previous_6m,
        "insufficient_history": insufficient,
        "truncated": real_total > total_in_log,
    }


def count_loc(repo_path: str) -> dict:
    """统计跟踪文件数、代码行数、模块数、语言分布、顶层目录。"""
    try:
        proc = _run_git(["ls-files"], cwd=repo_path, timeout=90)
        files = [f for f in (proc.stdout or "").splitlines() if f.strip()]
    except Exception:
        files = []

    code_files: list[str] = []
    languages: dict[str, int] = {}
    top_dirs: dict[str, int] = {}

    for f in files:
        parts = f.split("/")
        if any(p in SKIP_DIRS for p in parts[:-1]):
            continue
        if len(parts) > 1:
            top_dirs[parts[0]] = top_dirs.get(parts[0], 0) + 1
        ext = Path(f).suffix.lower()
        if ext in CODE_EXTS:
            code_files.append(f)
            lang = LANG_BY_EXT[ext]
            languages[lang] = languages.get(lang, 0) + 1

    loc = 0
    for f in code_files[:4000]:  # 上限保护，避免超大仓库耗时
        try:
            p = Path(repo_path) / f
            if p.stat().st_size > 2 * 1024 * 1024:
                continue
            with open(p, "rb") as fh:
                loc += fh.read().count(b"\n")
        except Exception:
            continue

    return {
        "tracked_files": len(files),
        "loc": loc,
        "modules": len(code_files),
        "top_dirs": sorted(
            [{"name": k, "files": v} for k, v in top_dirs.items()],
            key=lambda x: (-x["files"], x["name"]),
        )[:10],
        "languages": sorted(
            [{"name": k, "files": v} for k, v in languages.items()],
            key=lambda x: (-x["files"], x["name"]),
        )[:6],
        "code_files": code_files,
    }


def find_readme(repo_path: str) -> tuple[str | None, str]:
    root = Path(repo_path)
    for name in README_NAMES:
        p = root / name
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""
            return name, text
    # 兜底：任意大小写 readme*
    for p in root.glob("[Rr][Ee][Aa][Dd][Mm][Ee]*"):
        if p.is_file():
            try:
                return p.name, p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return p.name, ""
    return None, ""


INSTALL_HINTS = (
    "install",
    "pip install",
    "npm install",
    "yarn add",
    "uv add",
    "poetry add",
    "go get",
    "cargo add",
    "usage",
    "使用方法",
    "安装",
)
EXAMPLE_HINTS = ("```", ">>>", "$ ", "example", "示例", "demo")


def analyze_readme(repo_path: str) -> dict:
    name, text = find_readme(repo_path)
    low = text.lower()
    return {
        "has_readme": name is not None,
        "readme_path": name,
        "readme_chars": len(text),
        "has_install": any(h in low for h in INSTALL_HINTS),
        "has_usage": any(h in low for h in ("usage", "使用方法", "如何", "quick start", "getting started")),
        "has_example": any(h in text for h in EXAMPLE_HINTS),
        "text": text,
    }


def scan_risks(repo_path: str, code_files: list[str], *, max_hits: int = 40) -> dict:
    """扫 TODO/FIXME/HACK/XXX 位置，作为「雷区」素材。"""
    hits: list[dict] = []
    counts = {"TODO": 0, "FIXME": 0, "HACK": 0, "XXX": 0}
    scanned = 0
    for f in code_files:
        if scanned >= 1500 or len(hits) >= max_hits:
            break
        p = Path(repo_path) / f
        try:
            if p.stat().st_size > 1024 * 1024:
                continue
            with open(p, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        scanned += 1
        for i, line in enumerate(lines, 1):
            m = HOTSPOT_RE.search(line)
            if not m:
                continue
            kind = m.group(1)
            counts[kind] = counts.get(kind, 0) + 1
            if len(hits) < max_hits:
                hits.append(
                    {
                        "path": f,
                        "line": i,
                        "kind": kind,
                        "text": (m.group(2) or "").strip()[:100],
                    }
                )
            break  # 每个文件只取第一处，保证路径多样性
    return {"hits": hits, "counts": counts}


def file_history(repo_path: str, *, max_commits: int = MAX_COMMITS_ANALYZED) -> dict[str, dict]:
    """一次 git log 拿到所有文件的「最后改动时间 + 改动次数」。

    早期实现是每个文件跑两次 `git log -- <path>`，60 个文件就是 120 个进程，
    单仓库要多花 40-50 秒。这里改成单次遍历：`git log --format=%cI --name-only`，
    边读边累加。语义等价，速度快一个数量级。
    """
    try:
        proc = _run_git(
            ["log", f"--max-count={max_commits}", "--format=\x02%cI", "--name-only"],
            cwd=repo_path,
            timeout=120,
        )
    except Exception:
        return {}
    out: dict[str, dict] = {}
    current_date: str | None = None
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("\x02"):
            current_date = line[1:].strip()
            continue
        if current_date is None:
            continue
        rec = out.get(line)
        if rec is None:
            # log 是从新到旧，第一次遇到就是最后一次改动
            date_part = current_date[:19]
            out[line] = {"last_date": date_part, "commits": 1}
        else:
            rec["commits"] += 1
    return out


def stale_core_files(
    repo_path: str,
    code_files: list[str],
    *,
    now: datetime,
    limit: int = 8,
    history: dict[str, dict] | None = None,
) -> list[dict]:
    """找「改得最多 + 最久没碰」的核心文件 —— 典型的接手雷区。

    排序：改动次数降序（核心程度）→ 未改动天数降序（腐烂程度）→ 路径（保证稳定排序）。
    """
    hist = history if history is not None else file_history(repo_path)
    candidates = set(code_files[:400])
    rows: list[dict] = []
    for path, rec in hist.items():
        if path not in candidates:
            continue
        try:
            dt = _as_utc(datetime.fromisoformat(rec["last_date"]))
        except Exception:
            continue
        rows.append(
            {
                "path": path,
                "days_untouched": max(0, (now - dt).days),
                "commits": rec["commits"],
                "loc": 0,
            }
        )

    rows.sort(key=lambda x: (-x["commits"], -x["days_untouched"], x["path"]))
    picked = rows[:limit]

    for row in picked:
        try:
            row["loc"] = (Path(repo_path) / row["path"]).read_bytes().count(b"\n")
        except Exception:
            row["loc"] = 0
    return picked


def dir_size_mb(path: str) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except Exception:
                continue
    return total / (1024 * 1024)


def last_commit_message(repo_path: str) -> str:
    try:
        p = _run_git(["log", "-1", "--pretty=format:%s"], cwd=repo_path, timeout=30)
        return (p.stdout or "").strip()
    except Exception:
        return ""


def cleanup(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def make_workdir() -> str:
    return tempfile.mkdtemp(prefix="requiem-")
