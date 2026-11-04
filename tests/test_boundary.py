"""分析引擎的边界情况测试（离线可跑，不访问网络）。

SPEC 第 5 章列的 7 种边界情况里，能在离线环境用本地夹具验证的都在这里：
空仓库、单 commit、非 Git 目录、坏地址、不存在的路径、不存在的远程仓库。

真实远程仓库的验收测试在 tests/test_live_repos.py（需要网络，默认跳过）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.analyzer import analyze_repo, parse_repo_input  # noqa: E402


def _git(args, cwd):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "http.proxy"
    env["GIT_CONFIG_VALUE_0"] = ""
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env
    )


class TestParseInput(unittest.TestCase):
    def test_https_github(self):
        info = parse_repo_input("https://github.com/kornia/kornia")
        self.assertEqual((info["host"], info["owner"], info["name"]), ("github", "kornia", "kornia"))

    def test_https_with_dot_git_and_slash(self):
        info = parse_repo_input("https://github.com/cslkkl/dsh-cpa-switch.git/")
        self.assertEqual(info["name"], "dsh-cpa-switch")

    def test_ssh(self):
        info = parse_repo_input("git@github.com:kornia/kornia.git")
        self.assertEqual((info["host"], info["owner"], info["name"]), ("github", "kornia", "kornia"))

    def test_shorthand(self):
        info = parse_repo_input("cslkkl/dsh-cpa-switch")
        self.assertEqual(info["url"], "https://github.com/cslkkl/dsh-cpa-switch.git")

    def test_gitlab(self):
        info = parse_repo_input("https://gitlab.com/gitlab-org/gitlab-foss")
        self.assertEqual(info["host"], "gitlab")

    def test_empty_is_invalid(self):
        self.assertEqual(parse_repo_input("")["host"], "invalid")

    def test_garbage_is_invalid(self):
        self.assertEqual(parse_repo_input("这不是一个地址")["host"], "invalid")


class TestLocalFixtures(unittest.TestCase):
    """用本地临时仓库验证 0-commit / 1-commit 两种情况。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="requiem-test-")
        # 空仓库：git init 后没有任何 commit
        cls.empty = os.path.join(cls.tmp, "empty")
        os.makedirs(cls.empty)
        _git(["init", "-b", "main"], cls.empty)

        # 单 commit 仓库
        cls.single = os.path.join(cls.tmp, "single")
        os.makedirs(cls.single)
        _git(["init", "-b", "main"], cls.single)
        _git(["config", "user.email", "solo@example.com"], cls.single)
        _git(["config", "user.name", "Solo Dev"], cls.single)
        Path(cls.single, "main.py").write_text("print('hello')\n" * 50, encoding="utf-8")
        Path(cls.single, "README.md").write_text("# Demo\n", encoding="utf-8")
        _git(["add", "."], cls.single)
        _git(["commit", "-m", "initial commit"], cls.single)

        # 非 Git 目录
        cls.plain = os.path.join(cls.tmp, "plain")
        os.makedirs(cls.plain)

    @classmethod
    def tearDownClass(cls):
        """必须清理：不清理会在 %TEMP% 里堆出几十个 requiem-test-* 目录。"""
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_empty_repo_returns_birth_message(self):
        m = analyze_repo(self.empty)
        self.assertFalse(m["ok"])
        self.assertIn("还没有出生", m["error"]["message"])

    def test_single_commit_ok_and_bus_factor_is_one(self):
        m = analyze_repo(self.single)
        self.assertTrue(m["ok"], m.get("error"))
        self.assertEqual(m["authors"]["bus_factor"], 1)
        self.assertEqual(m["trend"]["insufficient_history"], True)
        # SPEC 5：只有 1 个 commit 时趋势项按 10 分
        self.assertEqual(m["score"]["parts"]["trend"]["score"], 10)

    def test_non_git_directory_is_invalid(self):
        m = analyze_repo(self.plain)
        self.assertFalse(m["ok"])
        self.assertEqual(m["error"]["kind"], "invalid")

    def test_nonexistent_path_is_invalid(self):
        m = analyze_repo(os.path.join(self.tmp, "does-not-exist"))
        self.assertFalse(m["ok"])
        self.assertIn(m["error"]["kind"], ("invalid", "not_found"))

    def test_every_result_has_full_schema(self):
        """无论成功失败，调用方拿到的 dict 必须有全部键 —— 界面不该 KeyError。"""
        for url in (self.single, self.empty, self.plain, ""):
            m = analyze_repo(url)
            for key in (
                "ok",
                "error",
                "repo",
                "life",
                "size",
                "authors",
                "trend",
                "deps",
                "issues",
                "docs",
                "risks",
                "score",
                "elapsed_sec",
            ):
                self.assertIn(key, m, f"{key} missing for {url!r}")
            self.assertIn("mode", m["score"])

    def test_determinism_same_local_repo(self):
        """同一仓库同一时刻跑两次，总分必须完全一致（SPEC 硬约束）。"""
        fixed_now = datetime(2026, 11, 4, tzinfo=UTC)
        a = analyze_repo(self.single, now=fixed_now)
        b = analyze_repo(self.single, now=fixed_now)
        self.assertEqual(a["score"]["total"], b["score"]["total"])
        self.assertEqual(a["deterministic_key"], b["deterministic_key"])

    def test_local_repo_gets_no_github_only_penalty(self):
        """非 GitHub 仓库：issue 项按 1 分（SPEC 5 最后一条）。"""
        m = analyze_repo(self.single)
        self.assertEqual(m["issues"]["status"], "not_github")
        self.assertEqual(m["score"]["parts"]["issues"]["score"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
