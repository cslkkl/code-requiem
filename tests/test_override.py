"""死亡否决与满分表达的测试。

这两条规则是 49 个真实仓库标定后加的，来源与理由见
.agents/notes/2026-11-05-death-override.md。

核心命题：
  1. 停更超过两年的项目，历史指标再漂亮也不能判为「存活」或「承压」
  2. 满分时不能输出「最大失分项」这种空话
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import explain  # noqa: E402
import scoring  # noqa: E402


def _metrics(total: int, mode: str, days: int, parts: dict | None = None) -> dict:
    if parts is None:
        parts = {
            "last_commit": {"score": 30, "max": 30, "label": "最后提交距今", "detail": ""},
            "bus_factor": {"score": 25, "max": 25, "label": "Bus Factor", "detail": ""},
            "trend": {"score": 15, "max": 15, "label": "提交活跃度趋势", "detail": ""},
            "deps": {"score": 15, "max": 15, "label": "依赖健康度", "detail": ""},
            "issues": {"score": 10, "max": 10, "label": "Issue 响应", "detail": ""},
            "docs": {"score": 5, "max": 5, "label": "文档完整度", "detail": ""},
        }
    return {
        "repo": {"host": "github", "owner": "a", "name": "b", "is_local": False},
        "life": {
            "first_commit": "2015-01-01T00:00:00+00:00",
            "last_commit": "2020-01-01T00:00:00+00:00",
            "days_since_last_commit": days,
            "total_commits": 100,
            "age_text": "5 年",
            "span_days": 1800,
        },
        "authors": {"total": 10, "bus_factor": 3, "top": [{"name": "A", "share": 0.4}]},
        "trend": {"recent_6m": 10, "previous_6m": 10, "ratio": 1.0, "insufficient_history": False},
        "deps": {
            "status": "ok",
            "count": 5,
            "oldest_pkg": "x",
            "oldest_days": 10,
            "oldest_scope": "runtime",
            "oldest_runtime_pkg": "x",
            "oldest_runtime_days": 10,
        },
        "issues": {"status": "ok", "closed_count": 5, "open_count": 1, "median_days": 2.0},
        "docs": {
            "has_readme": True,
            "readme_path": "README.md",
            "readme_chars": 3000,
            "has_install": True,
            "has_usage": True,
            "has_example": True,
        },
        "risks": {"todo_count": 0, "fixme_count": 0, "stale_core_files": [], "hotspots": []},
        "score": {"total": total, "mode": mode, "parts": parts},
        "data_quality": {"complete": True, "unavailable_dimensions": [], "note": ""},
    }


class TestDeathOverrideConstants(unittest.TestCase):
    """否决线的取值必须和 scoring 的模式阈值对齐。"""

    def test_caps_land_in_correct_bands(self):
        # >730 天：封顶到 critical 下限 - 1，必须落进 dead
        self.assertEqual(scoring.decide_mode(scoring.CRITICAL_MIN - 1), "dead")
        # >180 天：封顶到 alive 下限 - 1，必须落进 critical
        self.assertEqual(scoring.decide_mode(scoring.ALIVE_MIN - 1), "critical")

    def test_cap_values(self):
        self.assertEqual(scoring.CRITICAL_MIN - 1, 29)
        self.assertEqual(scoring.ALIVE_MIN - 1, 69)


class TestVerdictHonesty(unittest.TestCase):
    """满分时不许说「最大失分项」。"""

    def test_perfect_score_says_no_deduction(self):
        m = _metrics(100, "alive", 0)
        v = explain.verdict(m)
        self.assertNotIn("最大失分项", v)
        self.assertIn("满分", v)

    def test_near_perfect_does_not_alarm(self):
        parts = {
            "last_commit": {"score": 30, "max": 30, "label": "最后提交距今", "detail": ""},
            "bus_factor": {"score": 25, "max": 25, "label": "Bus Factor", "detail": ""},
            "trend": {"score": 15, "max": 15, "label": "提交活跃度趋势", "detail": ""},
            "deps": {"score": 13, "max": 15, "label": "依赖健康度", "detail": ""},
            "issues": {"score": 10, "max": 10, "label": "Issue 响应", "detail": ""},
            "docs": {"score": 5, "max": 5, "label": "文档完整度", "detail": ""},
        }
        m = _metrics(98, "alive", 0, parts)
        v = explain.verdict(m)
        self.assertNotIn("最大失分项", v)
        self.assertIn("不需要特别处理", v)

    def test_real_deduction_is_named(self):
        parts = {
            "last_commit": {"score": 30, "max": 30, "label": "最后提交距今", "detail": ""},
            "bus_factor": {"score": 0, "max": 25, "label": "Bus Factor", "detail": ""},
            "trend": {"score": 15, "max": 15, "label": "提交活跃度趋势", "detail": ""},
            "deps": {"score": 15, "max": 15, "label": "依赖健康度", "detail": ""},
            "issues": {"score": 10, "max": 10, "label": "Issue 响应", "detail": ""},
            "docs": {"score": 5, "max": 5, "label": "文档完整度", "detail": ""},
        }
        m = _metrics(75, "alive", 0, parts)
        v = explain.verdict(m)
        # verdict 用中文问法（explain.DIM_LABELS），不是 SPEC 术语
        self.assertIn("靠几个人撑着", v)
        self.assertIn("25", v)


class TestAnswersCoverAllStates(unittest.TestCase):
    """三个实际问题在任何状态下都必须给得出答案。"""

    def test_all_modes_produce_three_answers(self):
        for total, mode, days in ((95, "alive", 1), (55, "critical", 100), (10, "dead", 900)):
            m = _metrics(total, mode, days)
            answers = explain.practical_answers(m)
            with self.subTest(mode=mode):
                self.assertEqual(len(answers), 3)
                for a in answers:
                    self.assertTrue(a["question"])
                    self.assertTrue(a["answer"])
                    self.assertTrue(a["detail"])
                    self.assertIn(a["tone"], ("ok", "warn", "bad", "neutral"))

    def test_single_maintainer_is_called_out(self):
        m = _metrics(54, "critical", 1)
        m["authors"] = {"total": 1, "bus_factor": 1, "top": [{"name": "Solo", "share": 1.0}]}
        answers = explain.practical_answers(m)
        who = [a for a in answers if "找谁" in a["question"]][0]
        self.assertEqual(who["tone"], "bad")
        self.assertIn("Solo", who["detail"])
        self.assertIn("这个人离开，项目即终止", who["detail"])

    def test_dead_project_says_do_not_build_on_it(self):
        m = _metrics(10, "dead", 900)
        answers = explain.practical_answers(m)
        use = [a for a in answers if "还能用" in a["question"]][0]
        self.assertEqual(use["tone"], "bad")
        self.assertIn("静态遗产", use["detail"])
        self.assertIn("不要在其上做新功能", use["detail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
