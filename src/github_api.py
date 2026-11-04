"""GitHub 公开 REST API 的轻量封装（带磁盘缓存与配额感知）。

用途（都是「让分数更准确」而不是「让分数好看」）：
  1. 仓库元信息：真实创建时间（浅克隆拿不到真正的首个 commit）、是否已归档。
  2. 提交数统计：用 `per_page=1` + `Link: rel="last"` 头读出区间内的精确提交数。
     一次请求即可，比 `git log` 翻页便宜得多，也不受 `--depth` 限制。
  3. Issue 响应时间。

配额是这里最脆弱的地方：匿名只有 **60 次/小时**，一次完整分析要 6-14 次请求。
所以有两个必备设计：
  * **磁盘缓存**：元信息 10 分钟、计数 30 分钟、issue 15 分钟。重复分析与多人访问
    同一仓库时不会重复烧配额，同时也让「同一仓库两次同分」更容易成立。
  * **配额感知的错误**：403/429 时返回结构化 `rate_limited`，绝不谎报「仓库不存在」。

想提高配额：设置环境变量 `GITHUB_TOKEN`（HF Spaces 里放 Secrets）。有效 token 是 5000 次/小时。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import time
from datetime import datetime
from pathlib import Path

import requests

HTTP_TIMEOUT = 8
_UA = {"User-Agent": "code-requiem/1.0", "Accept": "application/vnd.github+json"}
API = "https://api.github.com"

_CACHE_DIR = Path(os.environ.get("REQUIEM_CACHE_DIR", str(Path.home() / ".cache" / "code-requiem")))
_TTL = {"meta": 600, "count": 1800, "issues": 900}

# 最近一次失败原因，供界面与日志展示
LAST_ERROR: str | None = None
RATE_LIMITED: bool = False


def _headers() -> dict:
    h = dict(_UA)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def has_token() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))


def _ck(key: str) -> Path:
    return _CACHE_DIR / f"gh-{hashlib.sha256(key.encode()).hexdigest()[:20]}.json"


def _cache_get(key: str, ttl: int):
    try:
        p = _ck(key)
        if not p.is_file():
            return None
        if time.time() - p.stat().st_mtime > ttl:
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_put(key: str, value) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _ck(key).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def rate_limit_status() -> dict:
    """读一次 /rate_limit（这个端点不消耗配额）。失败返回空 dict。"""
    try:
        r = requests.get(f"{API}/rate_limit", timeout=HTTP_TIMEOUT, headers=_headers())
        if r.status_code != 200:
            return {}
        core = (r.json() or {}).get("resources", {}).get("core", {})
        return {
            "limit": core.get("limit"),
            "remaining": core.get("remaining"),
            "reset": core.get("reset"),
        }
    except Exception:
        return {}


def _get(path: str, params: dict | None = None):
    global LAST_ERROR, RATE_LIMITED
    try:
        r = requests.get(f"{API}{path}", params=params, timeout=HTTP_TIMEOUT, headers=_headers())
    except Exception as exc:
        LAST_ERROR = f"{type(exc).__name__}: {exc}"
        return None
    if r.status_code == 200:
        RATE_LIMITED = False
        return r
    if r.status_code in (403, 429):
        remaining = r.headers.get("X-RateLimit-Remaining")
        if remaining == "0" or "rate limit" in (r.text or "").lower():
            RATE_LIMITED = True
            LAST_ERROR = "GitHub API 配额已用完（匿名 60 次/小时）" + (
                "，当前已配置 GITHUB_TOKEN" if has_token() else "，配置 GITHUB_TOKEN 可提升到 5000 次/小时"
            )
        else:
            LAST_ERROR = "GitHub API 拒绝访问（403）"
    elif r.status_code == 404:
        LAST_ERROR = "仓库不存在或私有"
    else:
        LAST_ERROR = f"HTTP {r.status_code}"
    return None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def repo_meta(owner: str, repo: str) -> dict | None:
    """仓库元信息。失败返回 None。"""
    key = f"meta:{owner}/{repo}"
    cached = _cache_get(key, _TTL["meta"])
    if cached is not None:
        return cached or None

    r = _get(f"/repos/{owner}/{repo}")
    if r is None:
        return None
    try:
        d = r.json()
    except Exception:
        return None
    meta = {
        "created_at": d.get("created_at"),
        "pushed_at": d.get("pushed_at"),
        "updated_at": d.get("updated_at"),
        "stars": d.get("stargazers_count", 0),
        "forks": d.get("forks_count", 0),
        "open_issues": d.get("open_issues_count", 0),
        "archived": bool(d.get("archived")),
        "default_branch": d.get("default_branch", "main"),
        "size_kb": d.get("size", 0),
        "language": d.get("language"),
        "description": (d.get("description") or "")[:300],
        "license": ((d.get("license") or {}) or {}).get("spdx_id"),
        "topics": d.get("topics") or [],
    }
    _cache_put(key, meta)
    return meta


def _count_from_link(r) -> int | None:
    """从 Link 头里读 `rel="last"` 的 page 号，per_page=1 时它就是总条数。"""
    link = r.headers.get("Link") or r.headers.get("link")
    if not link:
        try:
            data = r.json()
            return len(data) if isinstance(data, list) else None
        except Exception:
            return None
    m = re.search(r'[?&]page=(\d+)[^>]*>;\s*rel="last"', link)
    if m:
        return int(m.group(1))
    try:
        data = r.json()
        return len(data) if isinstance(data, list) else None
    except Exception:
        return None


def commit_count(
    owner: str, repo: str, *, since: datetime | None = None, until: datetime | None = None
) -> int | None:
    """区间内的精确提交数。失败返回 None。"""
    tag = f"{since.isoformat() if since else '-'}|{until.isoformat() if until else '-'}"
    key = f"count:{owner}/{repo}:{tag}"
    cached = _cache_get(key, _TTL["count"])
    if isinstance(cached, dict):
        return cached.get("n")

    params: dict = {"per_page": 1}
    if since:
        params["since"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    if until:
        params["until"] = until.strftime("%Y-%m-%dT%H:%M:%SZ")
    r = _get(f"/repos/{owner}/{repo}/commits", params)
    if r is None:
        return None
    n = _count_from_link(r)
    if n is not None:
        _cache_put(key, {"n": n})
    return n


def analyze_issues(owner: str, repo: str, *, host: str = "github") -> dict:
    """issue 响应时间。SPEC 3.5：已关闭 issue 的 (closed_at - created_at) 中位数。

    实现决策：过滤掉带 pull_request 字段的条目 —— PR 的关闭时长反映代码评审速度，
    不是 issue 响应速度，混进来会把分数虚高。
    """
    empty = {"status": "not_github", "closed_count": 0, "open_count": 0, "median_days": None}
    if host != "github" or not owner or not repo:
        return empty

    key = f"issues:{owner}/{repo}"
    cached = _cache_get(key, _TTL["issues"])
    if cached is not None:
        return cached

    r = _get(
        f"/repos/{owner}/{repo}/issues",
        {"state": "all", "per_page": 100, "sort": "created", "direction": "desc"},
    )
    if r is None:
        return {
            "status": "unavailable",
            "closed_count": 0,
            "open_count": 0,
            "median_days": None,
            "note": LAST_ERROR,
            "rate_limited": RATE_LIMITED,
        }
    try:
        data = r.json()
    except Exception:
        return {
            "status": "unavailable",
            "closed_count": 0,
            "open_count": 0,
            "median_days": None,
            "note": "响应解析失败",
        }
    if not isinstance(data, list):
        return {"status": "unavailable", "closed_count": 0, "open_count": 0, "median_days": None}

    closed_durations: list[float] = []
    open_count = 0
    total_items = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        total_items += 1
        if item.get("pull_request"):
            continue
        state = item.get("state")
        created = _parse_dt(item.get("created_at"))
        if state == "closed":
            closed = _parse_dt(item.get("closed_at"))
            if created and closed and closed >= created:
                closed_durations.append((closed - created).total_seconds() / 86400.0)
        elif state == "open":
            open_count += 1

    if not closed_durations:
        result = {
            "status": "ok",
            "closed_count": 0,
            "open_count": open_count,
            "median_days": None,
            "note": "样本窗口内没有已关闭 issue",
        }
    else:
        result = {
            "status": "ok",
            "closed_count": len(closed_durations),
            "open_count": open_count,
            "median_days": round(float(statistics.median(closed_durations)), 1),
            "sampled": total_items >= 100,
        }
    _cache_put(key, result)
    return result
