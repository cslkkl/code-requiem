# 决策：分数与理由分两层，并修正 bus factor / 依赖的两处不严谨 (2026-11-05)

状态：生效

## 问题

维护者指出：打分不能「跟机器人一样无理由地乱搞」。具体有四个问题：

1. **只有一句话的 `detail`**：例如「最后一次提交在 0 天前（≤7 天档）」——
   只报落在哪个档位，没有说清为什么这个档位值这个分、对要接手的人意味着什么。
2. **bus factor 把机器人当人**：`dependabot[bot]` 的提交被算进分母。
   但 bus factor 的业务含义是「走掉几个**人**项目就没人管」，机器人不会离职。
3. **依赖维度不区分运行时与工具链**：kornia 因为一个文档工具
   `sphinx-autodoc-defaultargs` 停更 2012 天而被判 0/15，
   但真正的运行时依赖 `kornia_rs` 只有 38 天。总分因此显得比实际风险严重。
4. **数据缺失与数据很差混在一起**：issue 接口不可用时按 SPEC 记 1 分，
   看起来像「项目不响应」；同一个仓库 API 可用时 85 分、不可用时 71 分，
   用户会以为打分不稳定。

## 决策

### 1. 分层：数字归数字，人话归人话

- `src/scoring.py` **只算数字**，严格照 SPEC 第 3 章阈值表，可被 `tests/test_scoring.py` 逐条比对。
- `src/explain.py` **只写理由**，把同一批观测值转成三段：
  1. **事实**：具体数字、具体包名、具体文件路径
  2. **机制**：这条为什么被扣，说出规则本身让人能复核
  3. **影响**：对要接手的人意味着什么，现在该做什么
- 新增 `tests/test_explain.py`（28 个用例）：三段必须非空、事实必须含具体数字、
  禁止「较低/偏高/较差」这类无信息量词、bus factor=1 必须出现 SPEC 规定的原句。

### 2. bus factor 排除机器人

`git_metrics.is_bot()` 按 name 与 email 识别 dependabot / renovate / github-actions /
pre-commit-ci / mergify 等。机器人提交单独记在 `bot_commits`，不进入 `author_counts`。

### 3. 依赖区分 runtime / dev（但不改分数）

解析时给每个依赖打 `scope`：`pyproject` 的 `[project.dependencies]` 与 poetry 主依赖是
runtime；`optional-dependencies`、`[dependency-groups]`、poetry group、
`devDependencies` 是 dev；`requirements.txt` 按工具名启发式判断。

**分数仍按 SPEC 取「所有依赖里最老的」**（不改算法，不破坏 SPEC 3.8 的预期区间），
但 `deps` 里额外输出 `oldest_scope` / `oldest_runtime_pkg` / `oldest_runtime_days`，
让解释层能说清「这条扣分伤不伤运行时」。

### 4. 数据完整性显式标注

`metrics.data_quality` = `{complete, unavailable_dimensions, note}`。
分数仍按 SPEC 取保守值，但界面与解释层要说明「这是拿不到数据，不是项目差」。

## 替代方案（强制）

- **只把 `detail` 写长一点**：治不了根。写法一长就会混进判断，数字与措辞相互污染，
  最后没人能说清某个分数到底来自 SPEC 哪一行还是来自文案发挥。
- **改 SPEC 阈值让 kornia 的依赖不扣分**：会破坏 SPEC 3.8 给出的预期区间（kornia 75–90），
  而且「依赖停更是风险」这个判断本身没错，错的是没说清风险落在哪里。
- **把机器人也算人**：更简单，但会让 bus factor 系统性虚高，
  恰好掩盖了我们这个产品最该暴露的单点风险。
- **数据缺失时直接报错不返回分数**：违反 SPEC「任何情况下都要有输出」，
  而且用户真正想要的是「能拿到的部分先看起来」。

## 影响

- 新增 28 个测试，总测试数 43 → 71。
- `metrics` 新增 `explain` / `verdict` / `top_deductions` / `data_quality` 四个字段，
  `docs/CONTRACT.md` 的 schema 需要同步（已更新）。
- 分数数值不变（bus factor 排除机器人后，个别仓库的 bus factor 会下降 1，
  但这是修正而非破坏 —— kornia 仍 85 分，v2ex-gae 仍 24 分）。
- 界面每个维度多两行「为什么 / 对你」，信息密度提高但首屏高度增加。
