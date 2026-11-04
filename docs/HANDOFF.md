# 交接说明 —— 2026-11-05 凌晨暂停

> 此文本可原样转发给新 agent 或明天的自己。

## 一句话现状

产品功能已完整（分析 → 打分 → 三种模式叙事 → 界面 → 文档），
但**页面编排还不好看、看不清**，这是明天第一优先级。
50 个仓库的标定跑到一半，已暴露三个必须修的算法问题（其中一个已修）。

## 今天的代码状态

- 已推送：`9247d57 docs: 同步分层决策与 schema` 及之前所有 commit
- **未提交**：bus factor 修正（`src/analyzer.py` / `src/github_api.py`），功能已验证但还没跑完整测试与推送
- 本地有未跟踪文件：`docs/calibration.json`、`scripts/calibrate.py`、`scripts/probe_contributors.py`

## 明天第一件事：重做页面编排（维护者原话「排版不好看，看不懂，不明确」）

当前问题诊断：

1. **没有主次**。总分、六维、八张指标卡、文档、解剖面板五段平铺，
   读者不知道该先看什么。
2. **信息不收敛**。六维每一项下面有「事实 / 为什么 / 对你」三行，
   六项就是 18 行文字，扫不动。
3. **术语直译**。「bus factor」「issue 中位响应」「依赖健康度」对非工程背景的
   评委不友好；而评委里可能有一半不是工程师。
4. **缺少结论前置**。最重要的那句话（这项目该不该接手、最大风险是什么）
   埋在一堆数字里。

建议方向（明天落地其一即可，不要全做）：

- **结论卡 + 三行摘要**：首屏只放「分数 / 状态 / 一句话结论 / 最大风险一条」，
  其余全部折叠。
- **六维改成表格式**：一行一维度，列是「维度 / 得分 / 一句话」。三行文字改成
  悬停展开或点击展开，默认只显示一句话。
- **术语旁边加中英对照**：`Bus Factor（走掉几个人项目就没人管）`。
- **把「雷区」和「接手顺序」提到文档之前**，因为那才是交接的真实产出。

## 明天第二件事：跑完 50 个仓库的标定

已跑 11 个（`docs/calibration.json`）。**已发现的三个真问题**：

| # | 问题 | 状态 |
|---|---|---|
| 1 | bus factor 用 200-300 条提交样本算，pandas（412 位贡献者）被判 bf=1，fastapi 被判只有 5 位作者 | **已修**：改用 GitHub contributors 接口的累计提交数，代码在 `src/github_api.py::contributors` |
| 2 | 若干大型活跃项目 deps 维度恒为 0（react / vue / rust / kornia），因为取「所有依赖里最老的」，命中的都是文档工具 | **待决策**：SPEC 要求取最老，但四个活跃大项目同时 0 分说明区分度失真。可考虑 dev 依赖权重减半，需同步改 SPEC 与测试 |
| 3 | issue 响应在限流时恒为 1 分，把好项目拉低 | 已加 `data_quality` 标注说明「这是假低」，但分数仍按 SPEC 取 1 |

未跑完的仓库：第 12 个开始（`torvalds/linux` 已超时跳过，属预期）。

跑法：
```powershell
$env:GITHUB_TOKEN = gh auth token
.venv\Scripts\python.exe -u scripts\calibrate.py --per-repo-timeout 240
```

## 明天第三件事：剩下三件交付物

- [ ] HF Spaces 部署（**卡在 `HF_TOKEN`，需要维护者提供**）
- [ ] 60-90 秒演示视频
- [ ] Devpost 七项提交（`docs/DEVPOST.md` 已备好，`[HF_SPACE_URL]` 待填）

## 已知坑（新 agent 必读）

- `github.com:443` 间歇不可达，`git push` 常失败 → 用 `scripts/push_via_api.py`
- 推送后本地与远端 sha 不一致 → 必须 `git fetch && git reset --hard origin/main`
- GitHub 匿名 API 60 次/小时，必须配 `GITHUB_TOKEN`
- 巨型仓库（torvalds/linux）能拖死进程 → 已加体积预检查与单仓库超时
- 进程被强杀会留临时目录 → 已有 `gm.cleanup_stale()`

## 验证入口

```powershell
.venv\Scripts\python.exe -u -m unittest discover -s tests     # 71 tests
.venv\Scripts\python.exe -m ruff check src/ app.py tests/ scripts/
.venv\Scripts\python.exe -u scripts\verify_live.py            # 三仓库三模式
```
