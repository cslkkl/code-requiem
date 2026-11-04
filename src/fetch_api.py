"""GitHub REST API 兜底数据源 —— 当 `git clone` 不可用时的替代采集路径。

为什么需要它：`github.com:443` 在某些网络（含本机当前网络）不可达，
但 `api.github.com` 与 `codeload.github.com` 正常。此时 git clone 必然失败，
而项目不能因此不可用 —— SPEC 要求「任何情况下都要有输出」。

代价（写进 README 的「已知限制」）：
  * 文件内容来自 tarball 快照，没有逐文件的完整改动历史；
    文件级「最后改动时间」用 `/commits?path=<file>&per_page=1` 逐个补，最多补 8 个文件。
  * 提交历史最多取 300 条（3 页 × 100），足够算 bus factor 与 6/6 月趋势。

数据形状与 git 路径保持一致，上层（analyzer）不需要知道数据是怎么来的。
"""

from __future__ import annotations

import io
import os
import re
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import requests

try:
    from . import net  # type: ignore
except ImportError:  # 脚本模式
    import net  # type: ignore

API = "https://api.github.com"
TIMEOUT = 20
MAX_PAGES = 3
PER_PAGE = 100
_UA = {"User-Agent": "code-requiem/1.0", "Accept": "application/vnd.github+json"}

# 配额耗尽标记：让上层能把「限流」和「仓库不存在」区分开
RATE_LIMITED: bool = False


def _note_status(status: int, body: str, headers) -> None:
    global RATE_LIMITED
    if status in (403, 429):
        remaining = headers.get("X-RateLimit-Remaining")
        if remaining == "0" or "rate limit" in (body or "").lower():
            RATE_LIMITED = True


def _headers() -> dict:
    h = dict(_UA)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except Exception:
        return None


def fetch_commits(owner: str, repo: str, *, max_pages: int = MAX_PAGES) -> list[dict]:
    """取最近提交列表（新→旧）。失败返回空列表。"""
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            r = requests.get(
                f"{API}/repos/{owner}/{repo}/commits",
                params={"per_page": PER_PAGE, "page": page},
                timeout=TIMEOUT,
                headers=_headers(),
            )
        except Exception:
            break
        if r.status_code != 200:
            _note_status(r.status_code, r.text, r.headers)
            break
        try:
            data = r.json()
        except Exception:
            break
        if not isinstance(data, list) or not data:
            break
        for item in data:
            if not isinstance(item, dict):
                continue
            commit = item.get("commit") or {}
            author = commit.get("author") or {}
            committer = commit.get("committer") or {}
            date = _dt(committer.get("date")) or _dt(author.get("date"))
            if date is None:
                continue
            email = (author.get("email") or (item.get("author") or {}).get("login") or "").lower()
            name = author.get("name") or (item.get("author") or {}).get("login") or email
            msg = (commit.get("message") or "").splitlines()
            out.append({
                "sha": item.get("sha", ""),
                "author": name,
                "email": email,
                "date": date,
                "subject": msg[0][:200] if msg else "",
            })
        if len(data) < PER_PAGE:
            break
    return out


def repo_meta(owner: str, repo: str) -> dict | None:
    try:
        r = requests.get(f"{API}/repos/{owner}/{repo}", timeout=TIMEOUT, headers=_headers())
        if r.status_code != 200:
            _note_status(r.status_code, r.text, r.headers)
            return None
        d = r.json()
    except Exception:
        return None
    return {
        "created_at": d.get("created_at"),
        "pushed_at": d.get("pushed_at"),
        "stars": d.get("stargazers_count", 0),
        "forks": d.get("forks_count", 0),
        "archived": bool(d.get("archived")),
        "default_branch": d.get("default_branch", "main"),
        "size_kb": d.get("size", 0),
        "description": (d.get("description") or "")[:300],
        "license": ((d.get("license") or {}) or {}).get("spdx_id"),
        "language": d.get("language"),
    }


def download_tarball(owner: str, repo: str, dest_dir: str, *, branch: str | None = None,
                     max_bytes: int = 400 * 1024 * 1024) -> str | None:
    """下载并解压 tarball 到 dest_dir/repo，返回仓库根目录路径。失败返回 None。"""
    ref = branch or "HEAD"
    urls = [
        f"https://codeload.github.com/{owner}/{repo}/tar.gz/{ref}",
        f"{API}/repos/{owner}/{repo}/tarball/{ref}",
    ]
    root = os.path.join(dest_dir, "repo")
    for url in urls:
        try:
            with requests.get(url, timeout=120, headers=_UA, stream=True, allow_redirects=True) as r:
                if r.status_code != 200:
                    continue
                os.makedirs(root, exist_ok=True)
                total = 0
                buf = io.BytesIO()
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        break
                    buf.write(chunk)
                if total == 0:
                    continue
                buf.seek(0)
                with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                    for member in tar.getmembers():
                        if not member.isfile():
                            continue
                        # 去掉 GitHub tarball 的 <owner>-<repo>-<sha>/ 前缀
                        parts = member.name.split("/", 1)
                        if len(parts) < 2 or not parts[1]:
                            continue
                        rel = parts[1]
                        if rel.startswith("/") or ".." in Path(rel).parts:
                            continue
                        target = Path(root) / rel
                        try:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            src = tar.extractfile(member)
                            if src is None:
                                continue
                            with open(target, "wb") as fh:
                                while True:
                                    block = src.read(1 << 20)
                                    if not block:
                                        break
                                    fh.write(block)
                        except Exception:
                            continue
                return root
        except Exception:
            continue
    return None


def file_last_commit_days(owner: str, repo: str, path: str, *, now: datetime) -> tuple[int, int] | None:
    """单个文件的 (距最后一次改动的天数, 改动次数)。各用 1 个请求，失败返回 None。"""
    try:
        r = requests.get(
            f"{API}/repos/{owner}/{repo}/commits",
            params={"path": path, "per_page": 1},
            timeout=TIMEOUT,
            headers=_headers(),
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    head = data[0] if isinstance(data[0], dict) else {}
    commit = head.get("commit") or {}
    date = _dt((commit.get("committer") or {}).get("date")) or _dt((commit.get("author") or {}).get("date"))
    if date is None:
        return None

    count = 1
    link = r.headers.get("Link") or ""
    m = re.search(r'[?&]page=(\d+)[^>]*>;\s*rel="last"', link)
    if m:
        count = int(m.group(1))
    return max(0, (now - date).days), count


def can_use_api() -> bool:
    return net.tcp_open("api.github.com")
