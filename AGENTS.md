# Code Requiem — 维护索引

## 项目形态

L3 标准模块：Python 包 + src/ + tests/ + 文档网络 + CI。

## 全局规则

- 分析层（打分）**零 AI、零随机、零 LLM** —— 同一仓库两次运行必须同分（见 `tests/test_boundary.py::test_determinism_same_local_repo`）
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

- 单元测试：43 passed（2026-11-04）
- Lint：ruff clean（2026-11-04）
- 真实仓库验证：kornia/kornia → 93 分 / dsh-cpa-switch → 56 分 / facebookarchive/caffe2 → 14 分

## 活跃坑

- **github.com:443 间歇性不可达**：已加 TCP 探测 + API 兜底，见 `src/net.py` / `src/fetch_api.py`
- **GitHub 匿名 API 限流 60 次/小时**：已加磁盘缓存 + 配额感知，配置 `GITHUB_TOKEN` 提升到 5000 次/小时
- **Windows GBK 控制台**：所有 Python 入口强制 UTF-8（`sys.stdout.reconfigure`），PowerShell 需 `chcp 65001`
- **uv tool run 在 Windows 首次极慢**：lint 改用本地 venv 里的 ruff

## 文档地图

- 架构设计 → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 模块契约 → [docs/CONTRACT.md](docs/CONTRACT.md)
- 决策与依据 → [.agents/notes/](.agents/notes/)
- 提交材料 → [docs/DEVPOST.md](docs/DEVPOST.md)
- 演示脚本 → [docs/DEMO.md](docs/DEMO.md)
- 实现规格 → [docs/SPEC.md](docs/SPEC.md)（唯一事实来源）
