# Code Requiem 架构说明

## 不可破坏约束

1. **分析层零 AI**：打分由 `src/scoring.py` 的纯函数完成，不调用任何 LLM，不使用随机数。同一仓库在同一时刻两次运行，健康分必须完全相同。
2. **任何情况下都有输出**：空仓库、单 commit、私有仓库、网络失败、LLM 挂了 —— 全部有输出，不允许未捕获异常导致页面白屏。
3. **密钥不出仓库**：所有 API key 走环境变量，部署时放 Hugging Face Spaces Secrets。

## 数据流

```
用户输入仓库地址
    ↓
src/analyzer.py（纯确定性）
    ├─ 采集：git clone --depth 200 或 GitHub API 兜底
    ├─ 计算：6 维打分 → 健康分 0-100
    └─ 判定：≥70 alive / 30-69 critical / <30 dead
    ↓
src/narrative.py（唯一允许调用 LLM 的层）
    ├─ alive → 代码遗嘱（5 章）
    ├─ critical → 遗嘱 + 悼词叠加
    ├─ dead → 项目悼词（5 章）
    └─ 失败 → 降级为结构化指标 Markdown
    ↓
app.py（Streamlit 界面）
    ├─ 总分 + 六维分数条
    ├─ 关键指标卡片
    └─ Markdown 文档渲染
```

## 网络兜底策略

这台机器上 `github.com:443` 间歇性不可达，但 `api.github.com` 与 `codeload.github.com` 正常。

策略：先 TCP 探测（3 秒），通就走 `git clone --depth 200`，不通立刻改走 GitHub REST API + tarball。

代价：API 路径下文件级历史只有前 8 个文件的「最后改动时间」，其余文件用目录扫描补齐。

## 配额与缓存

GitHub 匿名 API 限流 60 次/小时。一次完整分析要 6-14 个请求。

对策：
- 元信息缓存 10 分钟、提交计数 30 分钟、issue 15 分钟、依赖 7 天
- 失败结果缓存 6 小时（避免「一次成功一次失败」的分数漂移）
- 403/429 时返回 `rate_limited` 错误，绝不谎报「仓库不存在」

## 契约

模块间接口唯一事实来源：[CONTRACT.md](CONTRACT.md)。

改动契约必须先改 CONTRACT.md，并在 commit message 里写明。

## 防错清单

- [ ] 新增打分维度必须同时改 `scoring.py` + `tests/test_scoring.py`
- [ ] 新增 LLM 功能必须同时实现降级模板
- [ ] 新增网络调用必须有超时 + 缓存 + 失败降级
- [ ] 公开仓库新增文件必须检查不含密钥（`grep -r "hf_\|sk-\|ghp_\|api_key\|password"`）
