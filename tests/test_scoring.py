"""健康分算法单元测试 —— 逐条验证 docs/SPEC.md 第 3 章的阈值表。

这一层是纯函数，没有网络、没有随机、没有 LLM，所以断言可以写得很硬。
运行：.venv\\Scripts\\python.exe -u -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import scoring  # noqa: E402


class TestLastCommit(unittest.TestCase):
    """SPEC 3.1：0-7→30 / 8-30→25 / 31-90→18 / 91-180→10 / 181-365→5 / >365→0"""

    def test_boundaries(self):
        cases = [
            (0, 30), (7, 30),
            (8, 25), (30, 25),
            (31, 18), (90, 18),
            (91, 10), (180, 10),
            (181, 5), (365, 5),
            (366, 0), (3000, 0),
        ]
        for days, expected in cases:
            with self.subTest(days=days):
                got, _ = scoring.score_last_commit(days)
                self.assertEqual(got, expected)

    def test_negative_days_treated_as_zero(self):
        self.assertEqual(scoring.score_last_commit(-5)[0], 30)


class TestBusFactor(unittest.TestCase):
    """SPEC 3.2 给出的参考实现必须被逐字复现。"""

    def test_spec_example_single_dominant(self):
        # 一个人占 70% → bus factor 1
        self.assertEqual(scoring.bus_factor({"a": 70, "b": 20, "c": 10}), 1)

    def test_two_people(self):
        self.assertEqual(scoring.bus_factor({"a": 50, "b": 50}), 2)

    def test_exactly_sixty_percent(self):
        # 60/40 → 第一个就达到 >=0.6，返回 1
        self.assertEqual(scoring.bus_factor({"a": 60, "b": 40}), 1)

    def test_just_below_sixty(self):
        # 59/41 → 累计到第二个才 >=0.6
        self.assertEqual(scoring.bus_factor({"a": 59, "b": 41}), 2)

    def test_empty(self):
        self.assertEqual(scoring.bus_factor({}), 0)

    def test_many_equal_authors(self):
        counts = {chr(97 + i): 10 for i in range(10)}
        # 10 人各 10 → 累计到第 6 个人才到 60%
        self.assertEqual(scoring.bus_factor(counts), 6)

    def test_scores(self):
        self.assertEqual(scoring.score_bus_factor(1)[0], 0)
        self.assertEqual(scoring.score_bus_factor(2)[0], 10)
        self.assertEqual(scoring.score_bus_factor(3)[0], 17)
        self.assertEqual(scoring.score_bus_factor(4)[0], 25)
        self.assertEqual(scoring.score_bus_factor(9)[0], 25)

    def test_bus_factor_one_message(self):
        """SPEC 4.1 要求 bus factor=1 时必须出现固定句子。"""
        _, detail = scoring.score_bus_factor(1)
        self.assertIn("这个人离开，项目即终止", detail)


class TestTrend(unittest.TestCase):
    """SPEC 3.3：ratio >=1→15 / 0.5-0.99→10 / 0.1-0.49→5 / <0.1→0；历史不足 12 月→10"""

    def test_growing(self):
        self.assertEqual(scoring.score_trend(100, 50, False)[0], 15)

    def test_stable(self):
        self.assertEqual(scoring.score_trend(50, 50, False)[0], 15)

    def test_mild_decline(self):
        self.assertEqual(scoring.score_trend(50, 100, False)[0], 10)

    def test_steep_decline(self):
        self.assertEqual(scoring.score_trend(20, 100, False)[0], 5)

    def test_dead(self):
        self.assertEqual(scoring.score_trend(0, 100, False)[0], 0)

    def test_insufficient_history(self):
        self.assertEqual(scoring.score_trend(0, 0, True)[0], 10)

    def test_zero_previous_uses_one(self):
        # previous=0 时 max(0,1)=1，ratio=recent
        score, _, ratio = scoring.score_trend(3, 0, False)
        self.assertEqual(score, 15)
        self.assertEqual(ratio, 3.0)


class TestDeps(unittest.TestCase):
    """SPEC 3.4：<180→15 / 180-364→10 / 365-729→5 / >=730 或删除→0；无清单或失败→8"""

    def test_boundaries(self):
        cases = [(0, 15), (179, 15), (180, 10), (364, 10), (365, 5), (729, 5), (730, 0), (9999, 0)]
        for days, expected in cases:
            with self.subTest(days=days):
                self.assertEqual(scoring.score_deps(days, "ok")[0], expected)

    def test_neutral_cases(self):
        self.assertEqual(scoring.score_deps(None, "none")[0], 8)
        self.assertEqual(scoring.score_deps(None, "timeout")[0], 8)
        self.assertEqual(scoring.score_deps(None, "unsupported")[0], 8)

    def test_timeout_ignores_measured_days(self):
        """网络失败必须按 8 分，不能因为碰巧拿到天数就变成 15 分。"""
        self.assertEqual(scoring.score_deps(10, "timeout")[0], 8)


class TestIssues(unittest.TestCase):
    """SPEC 3.5：<7→10 / 7-29→6 / 30-89→3 / >=90→1；无数据→1"""

    def test_boundaries(self):
        cases = [(0.3, 10), (6.9, 10), (7, 6), (29, 6), (30, 3), (89, 3), (90, 1), (400, 1)]
        for median, expected in cases:
            with self.subTest(median=median):
                self.assertEqual(scoring.score_issues(median, "ok")[0], expected)

    def test_unavailable(self):
        self.assertEqual(scoring.score_issues(None, "unavailable")[0], 1)
        self.assertEqual(scoring.score_issues(3.0, "not_github")[0], 1)


class TestDocs(unittest.TestCase):
    """SPEC 3.6：README+安装+示例→5 / 只有 README>=500→3 / 很短→1 / 无→0"""

    def test_full(self):
        self.assertEqual(scoring.score_docs(True, 1000, True, True)[0], 5)

    def test_readme_only(self):
        self.assertEqual(scoring.score_docs(True, 800, True, False)[0], 3)
        self.assertEqual(scoring.score_docs(True, 800, False, False)[0], 3)

    def test_short(self):
        self.assertEqual(scoring.score_docs(True, 120, False, False)[0], 1)

    def test_none(self):
        self.assertEqual(scoring.score_docs(False, 0, False, False)[0], 0)


class TestMode(unittest.TestCase):
    """SPEC 3.7：>=70 alive / 30-69 critical / <30 dead"""

    def test_thresholds(self):
        self.assertEqual(scoring.decide_mode(100), "alive")
        self.assertEqual(scoring.decide_mode(70), "alive")
        self.assertEqual(scoring.decide_mode(69), "critical")
        self.assertEqual(scoring.decide_mode(30), "critical")
        self.assertEqual(scoring.decide_mode(29), "dead")
        self.assertEqual(scoring.decide_mode(0), "dead")


class TestMaxTotal(unittest.TestCase):
    """六维满分之和必须正好 100，否则分数区间会被压缩。"""

    def test_sum_is_100(self):
        total = (scoring.LAST_COMMIT_MAX + scoring.BUS_FACTOR_MAX + scoring.TREND_MAX
                 + scoring.DEPS_MAX + scoring.ISSUES_MAX + scoring.DOCS_MAX)
        self.assertEqual(total, 100)

    def test_perfect_score_reaches_alive(self):
        parts = [
            scoring.score_last_commit(0)[0],
            scoring.score_bus_factor(5)[0],
            scoring.score_trend(50, 50, False)[0],
            scoring.score_deps(10, "ok")[0],
            scoring.score_issues(1.0, "ok")[0],
            scoring.score_docs(True, 2000, True, True)[0],
        ]
        self.assertEqual(sum(parts), 100)
        self.assertEqual(scoring.decide_mode(sum(parts)), "alive")


if __name__ == "__main__":
    unittest.main(verbosity=2)
