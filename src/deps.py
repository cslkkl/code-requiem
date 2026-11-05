"""依赖健康度 —— 解析依赖清单并查询包管理器公开 API。

SPEC 3.4：
  Python  requirements.txt / pyproject.toml → https://pypi.org/pypi/{pkg}/json
  Node    package.json                      → https://registry.npmjs.org/{pkg}
  Go      go.mod                            → 跳过

取所有依赖中「最老的」那个距今天数作为最弱环节。
网络失败 / 5 秒超时 → 中性分 8（由 scoring 层处理）。
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

HTTP_TIMEOUT = 5  # SPEC 明确要求 5 秒
MAX_DEPS = 25  # 每次最多查这么多个，防止超大项目把总耗时拖爆
TOTAL_BUDGET_SEC = 25
WORKERS = 10

# 单个包响应体上限。PyPI 的 JSON 会带上全部历史版本与 README 全文，
# 像 numpy 这种包单次响应 3.6 MB、耗时 25 秒以上。超过上限就放弃这个包 ——
# 放弃是**固定策略**，所以同一仓库每次都会同样放弃，结果仍然确定。
MAX_BODY_BYTES = 3 * 1024 * 1024

# 包发布日期缓存：同一个包在 7 天内不会变，缓存能把重复分析的依赖耗时降到 0。
# 缓存也提升确定性 —— 同一仓库连续跑两次拿到的是同一份数据。
_CACHE_DIR = Path(os.environ.get("REQUIEM_CACHE_DIR", str(Path.home() / ".cache" / "code-requiem")))
_CACHE_TTL_SEC = 7 * 24 * 3600
# 失败结果只缓存 5 分钟：够吸收同一次分析里的重复查询，
# 又不至于让一次网络抖动在几小时里把某个包永久判成「查不到」。
_NEG_CACHE_TTL_SEC = 5 * 60


def _cache_path(url: str) -> Path:
    import hashlib

    h = hashlib.sha256(url.encode()).hexdigest()[:20]
    return _CACHE_DIR / f"{h}.json"


def _cache_get(key: str):
    try:
        p = _cache_path(key)
        if not p.is_file():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        # 负结果（days is None）只保留很短时间：一次网络抖动不应该让这个包
        # 在之后 6 小时里一直被当成「查不到」。实测出现过 certifi / httpcore /
        # anyio / idna 四个常用包被一次抖动固化，导致 httpx 的运行时依赖
        # 全部「查不到」，deps 维度直接掉到 0 分。
        if data.get("days") is None:
            ttl = _NEG_CACHE_TTL_SEC
        else:
            ttl = _CACHE_TTL_SEC
        if time.time() - p.stat().st_mtime > ttl:
            return None
        return data
    except Exception:
        return None


def _cache_put(key: str, value) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _cache_path(key)
        p.write_text(json.dumps(value), encoding="utf-8")
    except Exception:
        pass


_UA = {"User-Agent": "code-requiem/1.0 (dependency health check)"}

# requirements.txt 一行：pkg[extra]==1.2.3 ; python_version>="3"
REQ_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._\-]*)\s*(\[[^\]]*\])?\s*(.*)$")


# requirements.txt 里常见的开发/文档/测试工具名。
# 只在 requirements*.txt 这种无 scope 标注的清单里用作推断依据。
_DEV_TOOL_RE = re.compile(
    r"^(pytest|_pytest|pytest-.*|coverage|covdefaults|sphinx.*|pydata-sphinx-theme|"
    r"mkdocs.*|mkdocstrings.*|ruff|black|flake8|isort|pylint|mypy|pyright|pre-commit|"
    r"tox|nox|build|twine|wheel|setuptools|bump2version|bumpversion|"
    r"ipython|ipykernel|notebook|jupyter.*|nbconvert|"
    r"myst-parser|docutils|furo|sphinxcontrib-.*|"
    r"types-.*|mypy-.*|typeshed.*|"
    r"codespell|interrogate|vulture|radon|xenon)$",
    re.IGNORECASE,
)


def _parse_requirements(text: str) -> list[dict]:
    deps = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-") or line.startswith("git+"):
            continue
        if "://" in line:
            continue
        m = REQ_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        spec = (m.group(3) or "").strip()
        version = ""
        vm = re.search(r"==\s*([0-9][^\s,;]*)", spec)
        if vm:
            version = vm.group(1)
        if name.lower() in ("python", "pip", "setuptools", "wheel"):
            continue
        scope = "dev" if _DEV_TOOL_RE.match(name) else "runtime"
        deps.append({"name": name, "version": version, "scope": scope})
    return deps


def _parse_pyproject(text: str) -> list[dict]:
    """不引入 tomllib 之外的依赖；Python 3.11+ 有 tomllib，3.10 用正则兜底。

    每项带 scope：runtime（运行时必需）或 dev（开发/文档/测试工具）。
    为什么区分：文档构建工具停更 5 年，和运行时框架停更 5 年，对「接手风险」
    的意义完全不同。SPEC 只要求「取最老的那个」来打分，但解释时必须说清是哪一类。
    """
    deps: list[dict] = []
    try:
        import tomllib  # type: ignore

        data = tomllib.loads(text)
        project = data.get("project", {}) or {}
        for item in project.get("dependencies", []) or []:
            m = REQ_RE.match(str(item))
            if m:
                deps.append({"name": m.group(1), "version": "", "scope": "runtime"})
        optional = project.get("optional-dependencies", {}) or {}
        for _group, items in optional.items():
            for item in items or []:
                m = REQ_RE.match(str(item))
                if m:
                    deps.append({"name": m.group(1), "version": "", "scope": "dev"})
        # PEP 735 dependency-groups（现代项目把 dev 依赖放这里）
        groups = data.get("dependency-groups", {}) or {}
        for _gname, items in groups.items():
            for item in items or []:
                if isinstance(item, dict):
                    continue
                m = REQ_RE.match(str(item))
                if m:
                    deps.append({"name": m.group(1), "version": "", "scope": "dev"})
        poetry = (data.get("tool", {}) or {}).get("poetry", {}) or {}
        for name, spec in (poetry.get("dependencies", {}) or {}).items():
            if name.lower() == "python":
                continue
            ver = spec if isinstance(spec, str) else (spec or {}).get("version", "")
            deps.append({"name": name, "version": str(ver).lstrip("^~="), "scope": "runtime"})
        for _grp, group in (poetry.get("group", {}) or {}).items():
            for name, spec in (group.get("dependencies", {}) or {}).items():
                ver = spec if isinstance(spec, str) else (spec or {}).get("version", "")
                deps.append({"name": name, "version": str(ver).lstrip("^~="), "scope": "dev"})
    except Exception:
        deps = []
    if not deps:
        # 正则兜底
        m = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.S)
        if m:
            for name in re.findall(r"[\"']([A-Za-z0-9][A-Za-z0-9._\-]*)", m.group(1)):
                deps.append({"name": name, "version": "", "scope": "runtime"})
    return deps


def _parse_package_json(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except Exception:
        return []
    deps = []
    for key, scope in (
        ("dependencies", "runtime"),
        ("peerDependencies", "runtime"),
        ("devDependencies", "dev"),
    ):
        for name, ver in (data.get(key) or {}).items():
            if name.startswith("@types/"):
                continue
            deps.append({"name": name, "version": str(ver).lstrip("^~>=<"), "scope": scope})
    return deps


def detect_manifest(repo_path: str) -> dict:
    """返回 {"ecosystem": str, "file": str, "deps": [...]}"""
    root = Path(repo_path)

    py_req = root / "requirements.txt"
    if py_req.is_file():
        try:
            return {
                "ecosystem": "python",
                "file": "requirements.txt",
                "deps": _parse_requirements(py_req.read_text(encoding="utf-8", errors="replace")),
            }
        except Exception:
            pass

    py_proj = root / "pyproject.toml"
    if py_proj.is_file():
        try:
            deps = _parse_pyproject(py_proj.read_text(encoding="utf-8", errors="replace"))
            if deps:
                return {"ecosystem": "python", "file": "pyproject.toml", "deps": deps}
        except Exception:
            pass

    pkg = root / "package.json"
    if pkg.is_file():
        try:
            deps = _parse_package_json(pkg.read_text(encoding="utf-8", errors="replace"))
            return {"ecosystem": "node", "file": "package.json", "deps": deps}
        except Exception:
            pass

    go = root / "go.mod"
    if go.is_file():
        return {"ecosystem": "go", "file": "go.mod", "deps": []}

    return {"ecosystem": "none", "file": "", "deps": []}


def _get_json_capped(url: str, *, max_bytes: int = MAX_BODY_BYTES):
    """流式取 JSON，超过 max_bytes 就中止返回 None。

    用流式而不是 `r.json()` 的原因：requests 的 timeout 是「两次读之间的间隔」，
    持续下载的大响应体会一直刷新这个间隔，导致实际耗时远超 timeout。
    只有字节数上限能真正兜住耗时。
    """
    try:
        with requests.get(url, timeout=HTTP_TIMEOUT, headers=_UA, stream=True) as r:
            if r.status_code == 404:
                return {"_deleted": True}
            if r.status_code != 200:
                return None
            declared = r.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                return None
            buf = bytearray()
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    return None
            return json.loads(bytes(buf).decode("utf-8", errors="replace"))
    except Exception:
        return None


def _pypi_last_release_days(name: str, version: str) -> int | None:
    """查 PyPI，返回该包最后一个 release 距今天数。查不到返回 None。"""
    cache_key = f"pypi:{name}:{version}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached.get("days")

    days = _pypi_fetch(name, version)
    _cache_put(cache_key, {"days": days, "at": int(time.time())})
    return days


def _pypi_fetch(name: str, version: str) -> int | None:
    data = _get_json_capped(f"https://pypi.org/pypi/{name}/json")
    if data is None:
        return None
    if data.get("_deleted"):
        return -1  # 已从 registry 删除

    releases = data.get("releases") or {}
    best_dt = None
    if version and version in releases and releases[version]:
        for f in releases[version]:
            ts = f.get("upload_time_iso_8601") or f.get("upload_time")
            if ts:
                best_dt = ts
                break
    if best_dt is None:
        # 取所有文件的上传时间里最新的
        for _ver, files in releases.items():
            for f in files or []:
                ts = f.get("upload_time_iso_8601") or f.get("upload_time")
                if ts and (best_dt is None or ts > best_dt):
                    best_dt = ts
    if best_dt is None:
        return None
    try:
        dt = datetime.fromisoformat(best_dt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0, (datetime.now(UTC) - dt).days)
    except Exception:
        return None


def _npm_last_release_days(name: str, version: str) -> int | None:
    cache_key = f"npm:{name}:{version}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached.get("days")

    days = _npm_fetch(name, version)
    _cache_put(cache_key, {"days": days, "at": int(time.time())})
    return days


def _npm_fetch(name: str, version: str) -> int | None:
    data = _get_json_capped(f"https://registry.npmjs.org/{name.replace('/', '%2F')}")
    if data is None:
        return None
    if data.get("_deleted"):
        return -1
    times = data.get("time") or {}
    target = version or (data.get("dist-tags", {}) or {}).get("latest")
    ts = times.get(target) if target else None
    if not ts:
        ts = times.get("modified")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0, (datetime.now(UTC) - dt).days)
    except Exception:
        return None


def analyze_deps(repo_path: str) -> dict:
    """返回 CONTRACT.md 里的 deps 结构。永不抛异常。"""
    try:
        manifest = detect_manifest(repo_path)
    except Exception:
        return {
            "status": "error",
            "ecosystem": "none",
            "count": 0,
            "oldest_pkg": None,
            "oldest_days": None,
            "items": [],
            "file": "",
        }

    eco = manifest["ecosystem"]
    deps = manifest["deps"][:MAX_DEPS]

    if eco == "none":
        return {
            "status": "none",
            "ecosystem": "none",
            "count": 0,
            "oldest_pkg": None,
            "oldest_days": None,
            "items": [],
            "file": "",
        }
    if eco == "go" or not deps:
        return {
            "status": "unsupported",
            "ecosystem": eco,
            "count": len(manifest["deps"]),
            "oldest_pkg": None,
            "oldest_days": None,
            "items": [],
            "file": manifest["file"],
        }

    lookup = _pypi_last_release_days if eco == "python" else _npm_last_release_days
    items: list[dict] = []
    failures = 0

    def _work(d: dict):
        return d, lookup(d["name"], d.get("version", ""))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futures = [ex.submit(_work, d) for d in deps]
            for fut in concurrent.futures.as_completed(futures, timeout=TOTAL_BUDGET_SEC):
                try:
                    d, days = fut.result()
                except Exception:
                    failures += 1
                    continue
                if days is None:
                    failures += 1
                    continue
                items.append(
                    {
                        "name": d["name"],
                        "version": d.get("version", ""),
                        "days": days,
                        "scope": d.get("scope", "runtime"),
                    }
                )
    except concurrent.futures.TimeoutError:
        return {
            "status": "timeout",
            "ecosystem": eco,
            "count": len(deps),
            "oldest_pkg": None,
            "oldest_days": None,
            "items": items,
            "file": manifest["file"],
        }
    except Exception:
        return {
            "status": "error",
            "ecosystem": eco,
            "count": len(deps),
            "oldest_pkg": None,
            "oldest_days": None,
            "items": items,
            "file": manifest["file"],
        }

    if not items:
        # 一个都没查成 → 视作网络失败
        return {
            "status": "timeout" if failures else "none",
            "ecosystem": eco,
            "count": len(deps),
            "oldest_pkg": None,
            "oldest_days": None,
            "items": [],
            "file": manifest["file"],
        }

    # -1 表示已从 registry 删除，按 SPEC 视为最弱环节（≥730 天档）
    deleted = [i for i in items if i["days"] == -1]
    if deleted:
        oldest = deleted[0]
        return {
            "status": "ok",
            "ecosystem": eco,
            "count": len(deps),
            "oldest_pkg": oldest["name"],
            "oldest_days": 9999,
            "items": sorted(items, key=lambda x: (-x["days"], x["name"])),
            "file": manifest["file"],
            "deleted": [i["name"] for i in deleted],
        }

    oldest = max(items, key=lambda x: (x["days"], x["name"]))
    # 运行时依赖里最老的那个：解释层用它来判断「这条扣分是否真的影响运行」
    runtime_items = [i for i in items if i.get("scope") == "runtime"]
    oldest_runtime = max(runtime_items, key=lambda x: (x["days"], x["name"])) if runtime_items else None
    return {
        "status": "ok",
        "ecosystem": eco,
        "count": len(deps),
        "oldest_pkg": oldest["name"],
        "oldest_days": int(oldest["days"]),
        "oldest_scope": oldest.get("scope", "runtime"),
        "oldest_runtime_pkg": oldest_runtime["name"] if oldest_runtime else None,
        "oldest_runtime_days": int(oldest_runtime["days"]) if oldest_runtime else None,
        "runtime_count": len(runtime_items),
        "dev_count": len([i for i in items if i.get("scope") == "dev"]),
        "items": sorted(items, key=lambda x: (-x["days"], x["name"]))[:15],
        "file": manifest["file"],
    }
