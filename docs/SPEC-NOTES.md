# SPEC 与实现的差异记录

> `docs/SPEC.md` 仍是算法的**事实来源**。本文件只记录实现与 SPEC 字面表述的偏离，
> 每一条都来自 49 个真实仓库的标定，并附决策记录。

## 偏离 1：依赖健康度只按运行时依赖判分

| | 内容 |
|---|---|
| SPEC 3.4 原文 | 「取所有依赖中最老的那个距今天数（最弱环节）」 |
| 实现 | 优先取**运行时依赖**中最老的；解析失败时退回全量 |
| 理由 | dev 工具（sphinx / pytest / mkdocs）更新频率天然低，几乎每个成熟项目都被拖到 0 分，该维退化成抽奖 |
| 证据 | 49 个样本中 deps 零分 23 个（47%），标准差 5.27，六维里最差 |
| 决策 | [.agents/notes/2026-11-05-deps-runtime-only.md](../.agents/notes/2026-11-05-deps-runtime-only.md) |
| 分数影响 | flask 58 → 73；pandas/pydantic/kornia 85 → 100 |

## 偏离 2：新增「死亡否决」

| | 内容 |
|---|---|
| SPEC 3.7 原文 | 仅按总分判定：≥70 alive / 30-69 critical / <30 dead |
| 实现 | 在总分之上加两条否决：>730 天封顶 29（dead），>180 天封顶 69（critical） |
| 理由 | caffe2 停更 2960 天仍拿 44 分（承压），因为 bus factor 与历史 issue 响应各给满分。历史质量不能证明项目现在活着 |
| 证据 | 13/49 误判中，这一类（死仓库被评承压）在产品语义上最刺眼 |
| 决策 | [.agents/notes/2026-11-05-death-override.md](../.agents/notes/2026-11-05-death-override.md) |
| 分数影响 | caffe2 44 → 29，react-native-fbsdk 38 → 29 |

## 偏离 3：bus factor 用 contributors 接口而非提交样本

| | 内容 |
|---|---|
| SPEC 3.2 原文 | 「按 commit author email 去重」，算法按提交占比累加 |
| 实现 | 优先用 GitHub `contributors` 接口的累计提交数；拿不到时退回提交样本 |
| 理由 | `--depth 200` 或 300 条 API 样本里往往只有个位数作者，pandas（412 位贡献者）被判 bf=1 |
| 证据 | 修复前后：pandas 1 → 8，rust 7 → 31，numpy 5 → 11 |
| 分数影响 | bus factor 维度从「几乎人人满分或零分」变成真正有区分度（标准差 10.57） |

## 偏离 4：bus factor 排除机器人账号

| | 内容 |
|---|---|
| SPEC 3.2 原文 | 「按 commit author email 去重，不按 name」 |
| 实现 | 排除 dependabot / renovate / github-actions 等机器人 |
| 理由 | bus factor 的业务含义是「走掉几个**人**项目就没人管」，机器人不会离职 |
| 分数影响 | psf/requests 的 dependabot 占 20% 提交，排除后 bus factor 更接近真实 |

## 未偏离但需要说明的实现选择

- **issue 维度排除 PR**：SPEC 只说「已关闭 issue」，实现过滤了带 `pull_request`
  字段的条目，因为 PR 关闭时长反映评审速度而非 issue 响应速度。
- **享年用 `span_days`**（最后提交 - 首次提交）而非「到现在」，
  因为悼词里的「享年」指项目活跃期。
- **样本仓库的 bus factor 一律用全量贡献者**，即使仓库被采样。
