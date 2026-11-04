"""当 git push 走不通时，用 GitHub Git Data API 把本地 commit 推上去。

背景：这台机器 `github.com:443` 间歇不可达（git 协议走 443），
但 `api.github.com` 正常。所以 git push 会失败，而 REST API 能通。

做法：把本地相对远端多出的每个 commit，用 API 重放一遍
（blob → tree → commit → 更新 ref）。只推送差异文件，不重传整个仓库。

用法：
    .venv\\Scripts\\python.exe -u scripts\\push_via_api.py --dry-run
    .venv\\Scripts\\python.exe -u scripts\\push_via_api.py
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"
TIMEOUT = 30

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def token() -> str:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t.strip()
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except Exception as exc:
        raise SystemExit("拿不到 GITHUB_TOKEN：请先 gh auth login 或设置环境变量") from exc


def remote_repo() -> tuple[str, str]:
    url = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=ROOT, text=True).strip()
    # https://github.com/owner/repo.git
    part = url.rstrip("/").removesuffix(".git").split("github.com")[-1].strip("/:")
    owner, name = part.split("/")[:2]
    return owner, name


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8", errors="replace"
    ).strip()


def api(method: str, path: str, payload: dict | None = None, tok: str = "") -> dict:
    r = requests.request(
        method,
        f"{API}{path}",
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "code-requiem/1.0",
        },
        json=payload,
        timeout=TIMEOUT,
    )
    if r.status_code >= 300:
        raise SystemExit(f"{method} {path} → {r.status_code}: {r.text[:400]}")
    return r.json() if r.text else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--branch", default="main")
    args = ap.parse_args()

    tok = token()
    owner, name = remote_repo()

    # 远端当前 HEAD
    ref = api("GET", f"/repos/{owner}/{name}/git/refs/heads/{args.branch}", tok=tok)
    remote_sha = ref["object"]["sha"]
    local_head = git("rev-parse", "HEAD")

    if remote_sha == local_head:
        print("✅ 远端已是最新，无需推送")
        return 0

    # 远端 sha 是否是本地历史里的祖先
    try:
        behind = subprocess.run(["git", "merge-base", "--is-ancestor", remote_sha, local_head], cwd=ROOT)
        if behind.returncode != 0:
            raise SystemExit(f"远端 {remote_sha[:8]} 不在本地历史中，需要先 pull。本工具只负责快进推送。")
    except FileNotFoundError:
        pass

    commits = git("rev-list", "--reverse", f"{remote_sha}..{local_head}").splitlines()
    print(f"远端 HEAD: {remote_sha[:8]}")
    print(f"本地 HEAD: {local_head[:8]}")
    print(f"待推送 commit: {len(commits)} 个")
    for c in commits:
        print("  -", git("log", "-1", "--pretty=%h %s", c))

    if args.dry_run:
        print("\n[dry-run] 未做任何改动")
        return 0

    parent = remote_sha
    for c in commits:
        msg = git("log", "-1", "--pretty=%B", c)
        # 该 commit 相对父 commit 改动的文件
        changed = [f for f in git("diff-tree", "--no-commit-id", "--name-only", "-r", c).splitlines() if f]
        print(f"\n推送 commit {c[:8]}（{len(changed)} 个文件）")

        base_tree = api("GET", f"/repos/{owner}/{name}/git/commits/{parent}", tok=tok)["tree"]["sha"]
        tree_entries = []
        for path in changed:
            full = ROOT / path
            if full.is_file():
                content = full.read_bytes()
                blob = api(
                    "POST",
                    f"/repos/{owner}/{name}/git/blobs",
                    {"content": base64.b64encode(content).decode(), "encoding": "base64"},
                    tok=tok,
                )
                tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
            else:
                # 文件被删除
                tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})

        tree = api(
            "POST",
            f"/repos/{owner}/{name}/git/trees",
            {"base_tree": base_tree, "tree": tree_entries},
            tok=tok,
        )
        new_commit = api(
            "POST",
            f"/repos/{owner}/{name}/git/commits",
            {"message": msg, "tree": tree["sha"], "parents": [parent]},
            tok=tok,
        )
        parent = new_commit["sha"]
        print(f"  → {parent[:8]}")

    api(
        "PATCH",
        f"/repos/{owner}/{name}/git/refs/heads/{args.branch}",
        {"sha": parent, "force": False},
        tok=tok,
    )
    print(f"\n✅ 已推送 {len(commits)} 个 commit，远端 HEAD = {parent[:8]}")
    print("   本地 git 需执行：git fetch origin && git reset --hard origin/main（对齐远端 sha）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
