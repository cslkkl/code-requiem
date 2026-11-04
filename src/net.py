"""网络可达性探测。

背景（真实环境，不是假设）：这台机器上 `api.github.com` 与 `codeload.github.com`
可达，但 `github.com:443`（git 与网页域名）会连接超时，`huggingface.co` 同样超时。
`git clone https://github.com/...` 因此在坏路径上会白等 21 秒然后失败。

所以克隆前先做一次 3 秒的 TCP 探测：
  通 → 走 git clone（有完整文件级历史，最快）
  不通 → 立刻改走 GitHub REST API + tarball（慢一些，但一定拿得到数据）

探测结果在进程内缓存 60 秒，避免一次分析反复探测。
"""

from __future__ import annotations

import socket
import time

_CACHE: dict[str, tuple[float, bool]] = {}
TTL = 60.0


def tcp_open(host: str, port: int = 443, timeout: float = 3.0) -> bool:
    """TCP 是否可连。结果缓存 TTL 秒。"""
    key = f"{host}:{port}"
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < TTL:
        return hit[1]
    ok = False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ok = True
    except Exception:
        ok = False
    _CACHE[key] = (now, ok)
    return ok


def github_git_available() -> bool:
    """git clone github 仓库是否走得通。"""
    return tcp_open("github.com")


def github_api_available() -> bool:
    return tcp_open("api.github.com")


def codeload_available() -> bool:
    return tcp_open("codeload.github.com")


def reset_cache() -> None:
    _CACHE.clear()
