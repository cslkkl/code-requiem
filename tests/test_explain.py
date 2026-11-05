"""解释层单元测试 —— 验证每个维度都给出了「事实 + 机制 + 影响」三段。

这一层不产生分数，只产生人话。测试重点不是措辞，而是：
  1. 三段齐全，且都非空（否则退化成机器人报数）
  2. 事实里必须出现具体观测值（天数、包名、字符数），不能全是「偏低」「较差」
  3. 降级/缺失数据的情形必须被说清「这是拿不到，不是真的差」
  4. 解释层任何异常都不允许把分数搞坏
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import explain  # noqa: E402

DIMS = ["last_commit", "bus_factor", "trend", "deps", "issues", "docs"]

# 关键词黑名单：出现这些说明解释在糊弄，没有给具体事实
VAGUE = ["较低", "偏高", "较差", "一般般", "还行", "不太行"]


def _base_metrics(**over) -> dict:
    """构造一份健康仓库的完整指标，再按需覆盖。"""
    m = {
        "repo": {"host": "github", "owner": "acme", "name": "widget", "is_local": False},
        "life": {
            "first_commit": "2021-01-01T00:00:00+00:00",
            "last_commit": "2026-11-04T00:00:00+00:00",
            "last_commit_msg": "fix: handle empty input",
            "total_commits": 1200,
            "analyzed_commits": 300,
            "age_days": 2133,
            "span_days": 2133,
            "days_since_last_commit": 1,
            "age_text": "5 年 10 个月",
            "last_commit_age_text": "1 天",
        },
        "size": {"tracked_files": 120, "loc": 18000, "modules": 80, "top_dirs": [], "languages": []},
        "authors": {
            "total": 9,
            "bus_factor": 4,
            "top": [{"name": "Alice", "email": "a@x.com", "commits": 400, "share": 0.33}],
        },
        "trend": {"recent_6m": 120, "previous_6m": 100, "ratio": 1.2, "insufficient_history": False},
        "deps": {
            "status": "ok",
            "ecosystem": "python",
            "count": 8,
            "oldest_pkg": "requests",
            "oldest_days": 40,
            "oldest_scope": "runtime",
            "oldest_runtime_pkg": "requests",
            "oldest_runtime_days": 40,
            "items": [],
            "file": "pyproject.toml",
        },
        "issues": {
            "status": "ok",
            "closed_count": 30,
            "open_count": 4,
            "median_days": 2.5,
        },
        "docs": {
            "has_readme": True,
            "readme_path": "README.md",
            "readme_chars": 4000,
            "has_install": True,
            "has_usage": True,
            "has_example": True,
        },
        "risks": {"todo_count": 5, "fixme_count": 1, "stale_core_files": [], "hotspots": []},
        "score": {
            "total": 100,
            "mode": "alive",
            "parts": {
                "last_commit": {"score": 30, "max": 30, "label": "x", "detail": ""},
                "bus_factor": {"score": 25, "max": 25, "label": "x", "detail": ""},
                "trend": {"score": 15, "max": 15, "label": "x", "detail": ""},
                "deps": {"score": 15, "max": 15, "label": "x", "detail": ""},
                "issues": {"score": 10, "max": 10, "label": "x", "detail": ""},
                "docs": {"score": 5, "max": 5, "label": "x", "detail": ""},
            },
        },
        "data_quality": {"complete": True, "unavailable_dimensions": [], "note": ""},
    }
    for path, value in over.items():
        node = m
        keys = path.split("__")
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value
    return m


class TestThreePartStructure(unittest.TestCase):
    """每个维度都必须有 fact / mechanism / impact 三段，且都非空。"""

    def test_all_dimensions_have_three_parts(self):
        out = explain.explain_dimensions(_base_metrics())
        self.assertEqual(set(out), set(DIMS))
        for key in DIMS:
            e = out[key]
            with self.subTest(dim=key):
                for field in ("fact", "mechanism", "impact"):
                    self.assertIn(field, e)
                    self.assertTrue(str(e[field]).strip(), f"{key}.{field} 为空")

    def test_no_vague_wording(self):
        """解释里不许出现没有信息量的模糊词。"""
        out = explain.explain_dimensions(_base_metrics())
        for key, e in out.items():
            for field in ("fact", "mechanism", "impact"):
                text = str(e.get(field, ""))
                for word in VAGUE:
                    with self.subTest(dim=key, field=field, word=word):
                        self.assertNotIn(word, text)

    def test_fact_contains_concrete_number(self):
        """事实必须带具体数字，不能只有定性描述。"""
        out = explain.explain_dimensions(_base_metrics())
        for key, e in out.items():
            with self.subTest(dim=key):
                fact = str(e["fact"])
                self.assertTrue(
                    any(ch.isdigit() for ch in fact),
                    f"{key}.fact 里没有任何数字：{fact}",
                )


class TestLastCommitBands(unittest.TestCase):
    """SPEC 3.1 的六个档位，每个都要给出不同的解释。"""

    def test_bands_differ(self):
        cases = [
            (1, 30, "最强信号"),
            (20, 25, "放慢"),
            (60, 18, "散掉"),
            (120, 10, "转走"),
            (300, 5, "停更"),
            (800, 0, "弃养"),
        ]
        for days, _, keyword in cases:
            m = _base_metrics()
            m["life"]["days_since_last_commit"] = days
            m["score"]["parts"]["last_commit"]["score"] = next(s for d, s, _ in cases if d == days)
            e = explain.explain_dimensions(m)["last_commit"]
            with self.subTest(days=days):
                self.assertIn(keyword, e["mechanism"] + e["impact"])
                self.assertIn(str(days), e["fact"])


class TestBusFactor(unittest.TestCase):
    def test_bf1_must_say_person_leaves_project_dies(self):
        m = _base_metrics()
        m["authors"]["bus_factor"] = 1
        m["authors"]["top"] = [{"name": "Solo", "email": "s@x.com", "commits": 95, "share": 0.95}]
        e = explain.explain_dimensions(m)["bus_factor"]
        self.assertIn("这个人离开，项目即终止", e["impact"])
        self.assertIn("Solo", e["fact"])

    def test_bf1_mention_single_pillar(self):
        m = _base_metrics()
        m["authors"]["bus_factor"] = 1
        e = explain.explain_dimensions(m)["bus_factor"]
        self.assertIn("一根支柱", e["mechanism"])

    def test_bots_excluded_from_fact(self):
        """机器人提交次数要显式说明被排除，否则用户会算不明白占比。"""
        m = _base_metrics()
        m["life"]["bot_commits"] = 66
        e = explain.explain_dimensions(m)["bus_factor"]
        self.assertIn("66", e["fact"])
        self.assertIn("机器人", e["fact"])

    def test_bf4_is_healthy(self):
        e = explain.explain_dimensions(_base_metrics())["bus_factor"]
        self.assertIn("健康结构", e["mechanism"])


class TestTrend(unittest.TestCase):
    def test_insufficient_history_explained(self):
        m = _base_metrics()
        m["trend"]["insufficient_history"] = True
        e = explain.explain_dimensions(m)["trend"]
        self.assertIn("不满 12 个月", e["fact"])
        self.assertIn("10", e["mechanism"])

    def test_degraded_window_is_flagged(self):
        """API 不可用时必须说明趋势数字来源可疑。"""
        m = _base_metrics()
        m["data_quality"]["unavailable_dimensions"] = ["trend"]
        e = explain.explain_dimensions(m)["trend"]
        self.assertIn("不可用", e["fact"])

    def test_declining(self):
        m = _base_metrics()
        m["trend"] = {"recent_6m": 5, "previous_6m": 200, "ratio": 0.025, "insufficient_history": False}
        e = explain.explain_dimensions(m)["trend"]
        self.assertIn("停止提交", e["mechanism"])


class TestDepsScope(unittest.TestCase):
    """依赖维度最关键的判断：这条扣分到底伤不伤运行时。"""

    def test_dev_tool_old_is_softened(self):
        m = _base_metrics()
        m["deps"].update(
            {
                "oldest_pkg": "sphinx-tool",
                "oldest_days": 2012,
                "oldest_scope": "dev",
                "oldest_runtime_pkg": "torch",
                "oldest_runtime_days": 30,
            }
        )
        e = explain.explain_dimensions(m)["deps"]
        # 口径：dev 工具不参与打分，但必须如实说明它是技术债
        self.assertIn("开发/文档工具", e["fact"])
        self.assertIn("不计入分数", e["fact"])
        self.assertIn("torch", e["impact"])
        self.assertIn("锁版本", e["impact"])

    def test_runtime_dep_old_is_serious(self):
        m = _base_metrics()
        m["deps"].update(
            {
                "oldest_pkg": "legacy-lib",
                "oldest_days": 1500,
                "oldest_scope": "runtime",
                "oldest_runtime_pkg": "legacy-lib",
                "oldest_runtime_days": 1500,
            }
        )
        e = explain.explain_dimensions(m)["deps"]
        self.assertIn("运行时依赖", e["fact"])
        self.assertIn("定时炸弹", e["impact"])

    def test_timeout_says_not_real_conclusion(self):
        m = _base_metrics()
        m["deps"]["status"] = "timeout"
        e = explain.explain_dimensions(m)["deps"]
        self.assertIn("没查通", e["fact"])
        self.assertIn("没有真实结论", e["impact"])

    def test_no_manifest_is_actionable(self):
        m = _base_metrics()
        m["deps"]["status"] = "none"
        e = explain.explain_dimensions(m)["deps"]
        self.assertIn("依赖清单", e["fact"])
        self.assertIn("锁版本", e["impact"])


class TestIssues(unittest.TestCase):
    def test_unavailable_is_not_judged_as_bad(self):
        """拿不到数据时必须说明分数是假低，不能让人以为项目不响应。"""
        m = _base_metrics()
        m["issues"] = {"status": "unavailable", "closed_count": 0, "open_count": 0, "median_days": None}
        m["data_quality"]["unavailable_dimensions"] = ["issues"]
        e = explain.explain_dimensions(m)["issues"]
        self.assertIn("假低", e["impact"])

    def test_no_closed_issue_has_two_readings(self):
        m = _base_metrics()
        m["issues"] = {"status": "ok", "closed_count": 0, "open_count": 3, "median_days": None}
        e = explain.explain_dimensions(m)["issues"]
        self.assertIn("不是坏事", e["impact"])
        self.assertIn("没人管", e["impact"])

    def test_non_github_is_not_penalty_for_project(self):
        m = _base_metrics()
        m["repo"]["host"] = "gitlab"
        e = explain.explain_dimensions(m)["issues"]
        self.assertIn("不代表项目不响应", e["impact"])

    def test_fast_response(self):
        e = explain.explain_dimensions(_base_metrics())["issues"]
        self.assertIn("2.5", e["fact"])
        self.assertIn("10/10", e["mechanism"])


class TestDocs(unittest.TestCase):
    def test_missing_readme_is_actionable(self):
        m = _base_metrics()
        m["docs"]["has_readme"] = False
        e = explain.explain_dimensions(m)["docs"]
        self.assertIn("没有 README", e["fact"])
        self.assertIn("第一件事", e["impact"])

    def test_missing_parts_listed(self):
        m = _base_metrics()
        m["docs"].update({"has_install": False, "has_example": False})
        m["score"]["parts"]["docs"]["score"] = 3
        e = explain.explain_dimensions(m)["docs"]
        self.assertIn("安装/使用说明", e["fact"])
        self.assertIn("代码示例", e["fact"])


class TestSummary(unittest.TestCase):
    def test_top_deductions_ranks_by_lost_points(self):
        m = _base_metrics()
        m["score"]["parts"]["deps"]["score"] = 0  # 丢 15
        m["score"]["parts"]["docs"]["score"] = 3  # 丢 2
        m["score"]["parts"]["trend"]["score"] = 3  # 丢 12
        m["explain"] = explain.explain_dimensions(m)
        rows = explain.top_deductions(m, limit=2)
        self.assertEqual([r["key"] for r in rows], ["deps", "trend"])
        self.assertEqual(rows[0]["lost"], 15)
        self.assertTrue(rows[0]["reason"])

    def test_top_deductions_empty_when_perfect(self):
        m = _base_metrics()
        m["explain"] = explain.explain_dimensions(m)
        self.assertEqual(explain.top_deductions(m), [])

    def test_verdict_mentions_mode_and_worst_dim(self):
        m = _base_metrics()
        m["score"]["total"] = 54
        m["score"]["mode"] = "critical"
        m["score"]["parts"]["deps"]["score"] = 0
        m["explain"] = explain.explain_dimensions(m)
        v = explain.verdict(m)
        self.assertIn("承压", v)
        self.assertIn("依赖", v)

    def test_verdict_dead_project(self):
        m = _base_metrics()
        m["score"]["total"] = 12
        m["score"]["mode"] = "dead"
        m["life"]["days_since_last_commit"] = 900
        m["explain"] = explain.explain_dimensions(m)
        v = explain.verdict(m)
        self.assertIn("已经停了", v)
        self.assertIn("考古", v)


class TestRobustness(unittest.TestCase):
    """解释层绝不允许拖垮分析：输入残缺也要出结果。"""

    def test_empty_metrics(self):
        out = explain.explain_dimensions({})
        self.assertEqual(set(out), set(DIMS))

    def test_garbage_types(self):
        m = _base_metrics()
        m["life"]["days_since_last_commit"] = "not-a-number"
        m["trend"]["ratio"] = None
        out = explain.explain_dimensions(m)
        for key in DIMS:
            self.assertTrue(out[key]["fact"])

    def test_explainer_exception_is_caught(self):
        """模拟某个解释函数抛异常，其余维度仍要正常。"""
        original = explain._EXPLAINERS["docs"]
        try:
            explain._EXPLAINERS["docs"] = lambda _m: (_ for _ in ()).throw(RuntimeError("boom"))
            out = explain.explain_dimensions(_base_metrics())
            self.assertIn("boom", out["docs"]["mechanism"])
            self.assertTrue(out["last_commit"]["fact"])  # 其它维度不受影响
        finally:
            explain._EXPLAINERS["docs"] = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
