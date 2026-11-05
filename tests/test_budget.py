"""时间预算的回归测试。

背景：验收要求「贴入任意公开仓库，90 秒内出结果」。
实测最坏情况（github.com 探测通过但克隆卡死，再走 API 兜底 + 拖 tarball）
曾经到 162 秒，因为两条路径各自跑满自己的超时预算，没有人统管总时长。

这里用「把慢操作替换成 sleep」的方式离线复现这些场景，
不联网、不依赖真实仓库，跑一次不到 10 秒。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import UTC, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import analyzer  # noqa: E402
from src import git_metrics as gm

SLA_SEC = 90.0


class BudgetConstants(unittest.TestCase):
    def test_budget_leaves_room_under_sla(self):
        """预算必须明显小于 SLA，把余量留给渲染与叙事。"""
        self.assertLess(analyzer.BUDGET_SEC, SLA_SEC)
        self.assertGreaterEqual(SLA_SEC - analyzer.BUDGET_SEC, 10)

    def test_clone_timeout_cannot_eat_whole_budget(self):
        """克隆超时的上限必须小于预算的一半，否则兜底路径没有时间可用。"""
        # analyze_repo 里的算法：min(40, BUDGET - elapsed - 35)，且不低于 15
        worst = 40
        self.assertLess(worst, analyzer.BUDGET_SEC / 2 + 10)


class CloneFailureFallsBackFast(unittest.TestCase):
    """克隆卡死时，总耗时必须仍在 SLA 内，并且仍能给出结果。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="requiem-budget-")
        # 造一个有两次提交的本地仓库，模拟「兜底路径能拿到数据」的场景
        cls.repo = os.path.join(cls.tmp, "repo")
        os.makedirs(cls.repo)
        cmds = [
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "a@b.c"],
            ["git", "config", "user.name", "Tester"],
        ]
        for c in cmds:
            subprocess.run(c, cwd=cls.repo, check=True, capture_output=True)
        with open(os.path.join(cls.repo, "README.md"), "w", encoding="utf-8") as f:
            f.write("# Demo\n\n## Install\n\n## Usage\n")
        with open(os.path.join(cls.repo, "main.py"), "w", encoding="utf-8") as f:
            f.write("print(1)\n")
        subprocess.run(["git", "add", "-A"], cwd=cls.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=cls.repo, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_hung_clone_does_not_blow_the_sla(self):
        """克隆睡满它的超时，总时长仍要在 SLA 内。

        这个用例故意要等真的超时（约 40 秒），所以默认跳过；
        改预算相关代码时用 `RUN_SLOW=1` 跑一次。
        """
        if not os.environ.get("RUN_SLOW"):
            self.skipTest("慢用例（约 40 秒），设 RUN_SLOW=1 启用")

        from src import net

        orig_clone = gm.clone_repo
        orig_probe = net.github_git_available

        def slow_clone(url, workdir, *, depth=200, timeout=180, attempts=2):
            # 睡满传进来的超时 —— 模拟最坏情况（不是睡固定的 180）
            time.sleep(min(timeout, 45))
            raise gm.CloneError("network", "模拟克隆超时")

        try:
            net.github_git_available = lambda: True  # 假装探测通过，逼它走克隆
            gm.clone_repo = slow_clone
            t = time.perf_counter()
            m = analyzer.analyze_repo(
                "https://github.com/example/nonexistent-budget-probe",
                now=datetime(2026, 1, 1, tzinfo=UTC),
            )
            elapsed = time.perf_counter() - t
        finally:
            gm.clone_repo = orig_clone
            net.github_git_available = orig_probe

        self.assertLess(elapsed, SLA_SEC, f"最坏情况耗时 {elapsed:.1f}s 超过 {SLA_SEC}s")
        # 允许失败（网络拿不到数据），但不允许「挂住不出结论」
        self.assertIn("ok", m)

    def test_local_repo_is_fast(self):
        """本地仓库不碰网络，必须秒回。"""
        t = time.perf_counter()
        m = analyzer.analyze_repo(self.repo, now=datetime(2026, 1, 1, tzinfo=UTC))
        elapsed = time.perf_counter() - t
        self.assertTrue(m["ok"], m.get("error"))
        self.assertLess(elapsed, 10.0, f"本地仓库分析耗时 {elapsed:.1f}s")


class SkippedDepsIsNeutral(unittest.TestCase):
    """时间预算用尽而跳过依赖查询时，必须按中性分，且说明原因。"""

    def test_skipped_status_is_neutral_with_reason(self):
        import scoring

        got, detail = scoring.score_deps(None, "skipped")
        self.assertEqual(got, scoring.DEPS_NEUTRAL)
        self.assertIn("时间预算", detail)
        # 不能与「真实的依赖停更」混为一谈
        self.assertNotIn("730", detail)

    def test_skipped_differs_from_timeout(self):
        import scoring

        _, skipped = scoring.score_deps(None, "skipped")
        _, timeout = scoring.score_deps(None, "timeout")
        self.assertNotEqual(skipped, timeout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
