# Code Requiem — 维护索引

## 项目形态

L3 标准模块：Python 包 + src/ + tests/ + 文档网络 + CI。

## 全局规则

- 分析层（打分）**零 AI、零随机、零 LLM** —— 同一仓库两次运行必须同分（见 `tests/test_boundary.py::test_determinism_same_local_repo`）
- **分数与理由分两层**：`src/scoring.py` 只照 SPEC 算数字，`src/explain.py` 只写人话（事实 → 机制 → 影响）。改任一层都要同步各自的测试
- 所有 LLM 调用必须可降级：失败时输出结构化指标，绝不白屏（见 `src/narrative.py`）
- 密钥只走环境变量，绝不写进仓库（见 `.gitignore`）
- 架构与数据流 → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 决策与依据 → [.agents/notes/](.agents/notes/)
- 模块契约 → [docs/CONTRACT.md](docs/CONTRACT.md)

## 常用命令

```powershell
# 跑测试
.venv\Scripts\python.exe -u -m unittest discover -s tests -v

# Lint + Format
.venv\Scripts\python.exe -m ruff check --fix src/ app.py tests/
.venv\Scripts\python.exe -m ruff format src/ app.py tests/

# 文档链接校验
python C:\Users\11965\.agents\skills\maintenance-flow\check-markdown-links.py . --fragments --refs

# 行尾校验
python C:\Users\11965\.agents\skills\maintenance-flow\check-line-endings.py . --target lf

# 启动本地界面
.venv\Scripts\python.exe -u -m streamlit run app.py

# 命令行分析
.venv\Scripts\python.exe -u src\analyzer.py https://github.com/kornia/kornia

# push（本机 github 代理坏了，必须显式绕过）
git -c "http.https://github.com/.proxy=" push origin main
```

## 验证快照

- 单元测试：79 passed（2026-11-05，含 explain 28 + override 8）
- Lint：ruff clean（2026-11-05）
- 真实仓库验证（`scripts/verify_live.py`，结果见 [docs/verification.json](docs/verification.json)）
- 49 个真实仓库标定（`scripts/calibrate.py` + `scripts/analyze_calibration.py`，结果见 [docs/calibration.json](docs/calibration.json)）
  - 误判率 29% → 23%；剩余「误判」经复核多为人工标签不准而非算法错
  - 已修：bus factor 低估（pandas 1→8、rust 7→31）、依赖口径（flask 58→73）、死亡否决（caffe2 44→29）
  - 未决：issue/docs 两维几乎恒满分（缺区分度）、bus factor 权重偏重（活跃单人项目被判承压）
  - 完整结论 → [.agents/notes/calibration-findings.md](.agents/notes/calibration-findings.md)
- 确定性：同一仓库两次运行分数与指纹完全一致（已由 `test_boundary.py` 与 `verify_live.py` 双重验证）

## 待办

- [ ] HF Spaces 部署（需要 `HF_TOKEN`，尚未拿到）
- [x] 界面截图（4 张，docs/screenshots/）
- [ ] 60-90 秒演示视频
- [ ] Devpost 七项提交（`docs/DEVPOST.md` 已备好，`[HF_SPACE_URL]` 待填）

## 活跃坑

- **github.com:443 间歇性不可达**：已加 TCP 探测 + API 兜底（`src/net.py` / `src/fetch_api.py`）；
  `git push` 失败时用 `scripts/push_via_api.py`，见 [决策记录](.agents/notes/2026-11-05-push-via-github-api.md)
- **GitHub 匿名 API 限流 60 次/小时**：一次分析要 6-14 次请求。必须配 `GITHUB_TOKEN`
  （5000 次/小时），否则第 5 个用户就撞墙。错误已正确分类为 `rate_limited` 而非 `not_found`
- **Windows GBK 控制台**：所有 Python 入口强制 UTF-8（`sys.stdout.reconfigure`），
  PowerShell 需 `[Console]::OutputEncoding = [Text.Encoding]::UTF8`
- **`requests` 的 timeout 只是读空闲超时**：缓慢滴流的服务端会绕过它。LLM 调用额外包了
  线程 + `future.result` 硬墙（`src/narrative.py`）
- **同一个人多个 email 会被算成多个贡献者**：`dsh-cpa-switch` 里 `zlZayn` 与 `cslkkl`
  是同一人的两个邮箱，导致 bus factor 被判 2 而非 1。SPEC 要求按 email 去重，
  这是规格与现实的偏差，暂按 SPEC 执行

## 文档地图

- 架构设计 → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 模块契约 → [docs/CONTRACT.md](docs/CONTRACT.md)
- 源码模块手册 → [src/README.md](src/README.md)
- 测试覆盖与运行 → [tests/README.md](tests/README.md)
- 运维脚本 → [scripts/README.md](scripts/README.md)
- 决策与依据 → [.agents/notes/](.agents/notes/)
- 提交材料 → [docs/DEVPOST.md](docs/DEVPOST.md)
- 演示脚本 → [docs/DEMO.md](docs/DEMO.md)
- 实现规格 → [docs/SPEC.md](docs/SPEC.md)（唯一事实来源）
- 与 SPEC 的偏离记录 → [docs/SPEC-NOTES.md](docs/SPEC-NOTES.md)
- 验收结果 → [docs/verification.json](docs/verification.json)
