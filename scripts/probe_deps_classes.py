"""用真实 pyproject 内容验证 runtime/dev 分类与打分口径。

针对 httpx：它的 dependencies 里只有 4 个运行时依赖（certifi / httpcore / anyio / idna），
dev 组里有一堆 mkdocs 工具。预期：runtime 4 个能被解析出来并查到发布日期。
"""

import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import deps as deps_mod


def gh_file(slug: str, path: str) -> str | None:
    try:
        out = subprocess.check_output(
            ["gh", "api", f"repos/{slug}/contents/{path}", "--jq", ".content"],
            text=True,
            encoding="utf-8",
        ).strip()
    except Exception as exc:
        print(f"  gh api 失败: {exc}")
        return None
    try:
        return base64.b64decode("".join(out.split())).decode("utf-8")
    except Exception:
        return None


CASES = [
    ("encode/httpx", "pyproject.toml"),
    ("pallets/flask", "pyproject.toml"),
    ("Textualize/rich", "pyproject.toml"),
]


def main() -> int:
    for slug, path in CASES:
        print(f"=== {slug} ===")
        text = gh_file(slug, path)
        if not text:
            print("  拿不到文件，跳过")
            continue
        tmp = tempfile.mkdtemp(prefix="depcheck-")
        (Path(tmp) / path).write_text(text, encoding="utf-8")
        manifest = deps_mod.detect_manifest(tmp)
        runtime = [d for d in manifest["deps"] if d.get("scope") == "runtime"]
        dev = [d for d in manifest["deps"] if d.get("scope") == "dev"]
        print(
            f"  生态={manifest['ecosystem']} 总={len(manifest['deps'])} runtime={len(runtime)} dev={len(dev)}"
        )
        print(f"  runtime: {[d['name'] for d in runtime][:10]}")
        print(f"  dev(前5): {[d['name'] for d in dev][:5]}")
        result = deps_mod.analyze_deps(tmp)
        print(
            f"  结果: status={result['status']} oldest_all={result.get('oldest_pkg')}"
            f"({result.get('oldest_days')}d/{result.get('oldest_scope')})"
            f" oldest_runtime={result.get('oldest_runtime_pkg')}({result.get('oldest_runtime_days')}d)"
        )
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
