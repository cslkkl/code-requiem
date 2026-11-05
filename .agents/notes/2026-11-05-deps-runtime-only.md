# 决策：依赖健康只按运行时依赖判分 (2026-11-05)

状态：生效

## 问题

SPEC 3.4 原文：「解析依赖清单，逐个查询包管理器公开 API 获取最后一次发布时间……
取所有依赖中**最老的**那个距今天数（最弱环节）」。

标定 49 个真实仓库后发现这条规则整体失效：

| 仓库 | deps 得分 | 最老依赖 | 类型 |
|---|---|---|---|
| pallets/flask | 0/15 | sphinxcontrib-log-cabinet 2679 天 | dev |
| encode/httpx | 0/15 | mkdocs 796 天 | dev |
| pandas-dev/pandas | 15/15 | 102 天 | 碰巧是 runtime |
| facebook/react | 0/15 | — | dev |

原因：`sphinx` / `pytest` / `mkdocs` / `tox` 这类工具更新频率天然低于运行时库
（一个文档主题五年不更新很正常）。于是**几乎每个成熟 Python 项目都被它拖到 0 分**，
而碰巧运行时依赖最老的项目反而满分 —— 这一维退化成「谁有老年 dev 工具」的抽奖。

49 个样本里 deps 维度有 23 个零分（47%），标准差 5.27，是六维里区分度最差的一项。

## 决策

`src/analyzer.py::_build_metrics` 判分时：

1. 优先用 `deps.oldest_runtime_days`（运行时依赖里最老天数）走 `scoring.score_deps`
2. 没有区分出 runtime（解析失败 / 旧清单 / 非 Python 生态）时退回 `oldest_days`，
   保持与 SPEC 字面兼容
3. `detail` 里同时写出「按什么口径计」和「全量最老是多少」，不隐瞒
4. 解释层（`src/explain.py::_explain_deps`）把 dev 工具的情况单列说明：
   **不计分，但仍然是技术债**

## 替代方案（强制）

- **保留原口径，只在解释里软化**：分数仍然是 0，用户看到的还是「依赖健康度 0 分」，
  与「这个项目依赖维护得很好」的事实矛盾。解释再好也救不回一个错的数字。
- **dev 依赖权重减半**：引入非整数分数，六维不再可逐个整数比对，
  `tests/test_scoring.py` 的阈值断言全部要重写，收益不抵复杂度。
- **完全不看 dev 依赖**：会漏掉真实的构建链风险（文档构建挂了，接手人一样动不了）。
  现在的做法是「不计分但要说」。
- **把 dev 依赖单独做成第七维**：SPEC 定义为六维（满分 100），加第七维要重排全部权重，
  超出黑客松时间预算。

## 影响

- flask 58 → 73（critical → alive），这正是标定要解决的问题。
- pandas / pydantic / kornia 等 runtime 依赖新的项目从 85 提到 100。
  100 分是这个口径下的真实结果：六项都无可改进。
- SPEC 3.4 的字面表述与本实现不一致，已在 `docs/SPEC-NOTES.md` 标注差异与理由
  （SPEC 仍作为算法的事实来源，本文件记录唯一一处偏离）。
- `scoring.score_deps` 本身没改，它仍然是纯阈值函数；口径选择在 analyzer 层做，
  这样 `tests/test_scoring.py` 的断言全部保持有效。
