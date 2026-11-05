"""把本项目部署到 Hugging Face Spaces。

用法（PowerShell）：

    $env:HF_TOKEN = "hf_xxx"          # 必须有 write 权限，建 Space 用
    $env:GITHUB_TOKEN = gh auth token # 可选，但强烈建议带上
    .venv\\Scripts\\python.exe -u scripts\\deploy_hf.py --check

    # 只看要做哪些事，不真的上传
    .venv\\Scripts\\python.exe -u scripts\\deploy_hf.py --dry-run

    # 真的部署
    .venv\\Scripts\\python.exe -u scripts\\deploy_hf.py

为什么单独一个脚本而不是手动传文件：
  HF Spaces 要求仓库根目录有带 YAML frontmatter 的 README.md 才能识别 SDK，
  而本项目的 README.md 是给 GitHub 看的（20KB 项目说明）。
  直接把 frontmatter 塞进主 README 会让 GitHub 页面顶部多出一块元数据表。
  所以这里在临时目录里拼一份「只给 Space 用」的 README，不动主 README。

密钥处理：GITHUB_TOKEN 与 LLM 凭据**只写进 Space 的 Secrets**，
不进入上传的文件树。脚本在上传前会扫一遍文件内容确认没有泄漏。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
SPACE_NAME = "code-requiem"
DEFAULT_REPO_ID = os.environ.get("HF_SPACE_ID", f"{{username}}/{SPACE_NAME}")

SECRET_PATTERNS = [
    ("HF token", re.compile(r"hf_[A-Za-z0-9]{20,}")),
    ("OpenAI-ish key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("GitHub token", re.compile(r"gh[po]_[A-Za-z0-9]{20,}")),
]

# 只上传运行必需的东西。测试、标定数据、截图都不进 Space。
INCLUDE = [
    "app.py",
    "requirements.txt",
    "src",
    ".streamlit",
]
EXCLUDE_DIRS = {"__pycache__", ".git", ".venv", "tests", "docs", "scripts", ".agents"}

SPACE_README = """---
title: Code Requiem
emoji: 📜
colorFrom: gray
colorTo: yellow
sdk: streamlit
sdk_version: 1.66.1
app_file: app.py
pinned: false
license: mit
short_description: 给接手别人代码的人：确定性健康分 + AI 交接文档
---

# 代码安魂曲 · Code Requiem

输入任意公开 Git 仓库地址 → 用**纯确定性算法**打出 0–100 健康分 → 让 AI 把它写成一份能直接贴进交接单的文档。

- **≥70 分**：代码遗嘱（还在呼吸，交代清楚谁能接手）
- **30–69 分**：临终关怀（在承压，列出所有风险点）
- **<30 分**：项目悼词（已停更，只写下它曾经是什么）

## 为什么分数可信

六维打分完全确定：同一个仓库跑两次，分数一个数字都不会变。
分析层不调用任何大模型 —— AI 只负责把已经算好的事实写成人话，写不出来就退回结构化模板。

维度：还在维护吗 / 靠几个人撑着 / 提交在变多还是变少 / 依赖还在维护吗 / 提问有人回吗 / 有说明书吗

## 隐私

只读取公开仓库的公开数据。不收集访问者信息，不做任何持久化存储。

源码：[github.com/cslkkl/code-requiem](https://github.com/cslkkl/code-requiem)
"""


def _scan_secrets(folder: Path) -> list[str]:
    """上传前扫一遍，确认没有把密钥写进文件。"""
    hits: list[str] = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".parquet"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for label, pat in SECRET_PATTERNS:
            if pat.search(text):
                hits.append(f"{label} in {path.relative_to(folder)}")
    return hits


def build_stage() -> Path:
    """在临时目录里拼出要上传的文件树。"""
    stage = Path(tempfile.mkdtemp(prefix="hf-space-"))
    for name in INCLUDE:
        src = ROOT / name
        if not src.exists():
            print(f"  跳过（不存在）: {name}")
            continue
        dst = stage / name
        if src.is_dir():
            shutil.copytree(
                src,
                dst,
                ignore=shutil.ignore_patterns(*EXCLUDE_DIRS, "*.pyc", ".DS_Store"),
            )
        else:
            shutil.copy2(src, dst)

    # Space 专用的 README（带 frontmatter），不覆盖主 README
    (stage / "README.md").write_text(SPACE_README, encoding="utf-8")
    return stage


def _token() -> str | None:
    for key in ("HF_TOKEN", "HUGGINGFACE_API_KEY", "HUGGINGFACEHUB_API_TOKEN"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    # 兜底：读 hf CLI 的登录态
    try:
        from huggingface_hub import HfFolder

        return HfFolder.get_token()
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打包并检查，不上传")
    ap.add_argument("--check", action="store_true", help="只检查前置条件")
    ap.add_argument("--repo-id", default=None, help="覆盖 Space 地址，形如 user/code-requiem")
    ap.add_argument("--private", action="store_true", help="建成私有 Space")
    args = ap.parse_args()

    token = _token()
    print("=== 前置检查 ===")
    print(f"  HF token: {'已找到' if token else '缺失'}")

    gh = (os.environ.get("GITHUB_TOKEN") or "").strip()
    print(
        f"  GITHUB_TOKEN: {'已找到（部署后写入 Space Secret）' if gh else '缺失（匿名 API 只有 60 次/小时，第 5 个访客就会撞墙）'}"
    )

    if not token:
        print()
        print("缺少 HF token。三步拿到：")
        print("  1. 打开 https://huggingface.co/settings/tokens")
        print("  2. New token → 类型选 Write（建 Space 需要写权限）→ 命名 code-requiem")
        print("  3. 复制生成的 hf_ 开头的串，然后在 PowerShell 里执行：")
        print('       $env:HF_TOKEN = "hf_你的token"')
        return 2

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    try:
        who = api.whoami()
        username = who.get("name") or (who.get("user") or {}).get("name")
        print(f"  已登录: {username}")
    except Exception as exc:
        print(f"  token 校验失败: {exc}")
        return 2

    repo_id = args.repo_id or f"{username}/{SPACE_NAME}"
    print(f"  目标 Space: {repo_id}")

    print()
    print("=== 打包 ===")
    stage = build_stage()
    files = [p for p in stage.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(f"  {len(files)} 个文件，{total / 1024:.0f} KB")
    for p in sorted(files):
        print(f"    {p.relative_to(stage)}")

    leaks = _scan_secrets(stage)
    if leaks:
        print()
        print("  ✗ 扫描到疑似密钥，已中止：")
        for hit in leaks:
            print(f"      {hit}")
        shutil.rmtree(stage, ignore_errors=True)
        return 3
    print("  密钥扫描：零命中")

    if args.check:
        shutil.rmtree(stage, ignore_errors=True)
        print()
        print("前置检查通过。去掉 --check 即可真正部署。")
        return 0

    if args.dry_run:
        print()
        print(f"（dry-run）会创建 Space {repo_id}，并写入 secrets: GITHUB_TOKEN")
        print(f"（dry-run）临时目录: {stage}")
        return 0

    print()
    print("=== 创建 / 复用 Space ===")
    try:
        api.create_repo(
            repo_id=repo_id,
            repo_type="space",
            space_sdk="streamlit",
            private=args.private,
            exist_ok=True,
        )
        print(f"  就绪: https://huggingface.co/spaces/{repo_id}")
    except Exception as exc:
        print(f"  建 Space 失败: {exc}")
        shutil.rmtree(stage, ignore_errors=True)
        return 4

    print()
    print("=== 写入 Secrets ===")
    secrets = {}
    if gh:
        secrets["GITHUB_TOKEN"] = gh
    # Space 上的 AI 叙事走 HF Router，用同一个 token
    secrets["HF_TOKEN"] = token
    for key, val in secrets.items():
        try:
            api.add_space_secret(repo_id=repo_id, key=key, value=val)
            print(f"  {key}: 已写入")
        except Exception as exc:
            print(f"  {key}: 写入失败 {exc}（可稍后在 Space Settings → Secrets 手动加）")

    print()
    print("=== 上传文件 ===")
    try:
        api.upload_folder(
            folder_path=str(stage),
            repo_id=repo_id,
            repo_type="space",
            commit_message="deploy: 代码安魂曲 Code Requiem",
        )
        print("  上传完成")
    except Exception as exc:
        print(f"  上传失败: {exc}")
        shutil.rmtree(stage, ignore_errors=True)
        return 5

    shutil.rmtree(stage, ignore_errors=True)
    print()
    print("=== 完成 ===")
    print(f"  Space 页面: https://huggingface.co/spaces/{repo_id}")
    print(f"  应用地址:   https://{repo_id.replace('/', '-')}.hf.space")
    print("  首次构建约 2-4 分钟，看页面上的 Building 状态。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
