# 部署指南

> 目标：让评委点开一个链接就能用的应用，不需要装任何东西。

## 现状：为什么不再是 Hugging Face Spaces

2026-11 实测发现 HF Spaces 已不能免费跑 Streamlit 应用：

| 探测 | 结果 |
|---|---|
| `sdk=streamlit` | HTTP 400 `Invalid option: expected one of "gradio"\|"docker"\|"static"` |
| `sdk=gradio` | HTTP 402 「需要 PRO 订阅」 |
| `sdk=docker` | HTTP 402 「需要 PRO 订阅」 |
| `sdk=static` | HTTP 200，但 static 跑不了 Python 后端 |

同时查了 HF 上最热的 100 个 Space，SDK 分布只剩 `gradio 71 / static 17 / docker 12`，
Streamlit 已被移除。账号 `11965CSL` 不是 PRO，这条路走不通。

**改走 Streamlit Community Cloud**：官方免费、原生运行 Streamlit、
直接连 GitHub 公开仓库，比 HF 更省事。

---

## 方案 A（推荐）：Streamlit Community Cloud

### 前置条件（已全部满足）

| 条件 | 状态 |
|---|---|
| 代码在公开 GitHub 仓库 | ✅ `github.com/cslkkl/code-requiem` |
| 根目录有 `app.py` | ✅ |
| 根目录有 `requirements.txt` | ✅ 已锁定版本 |
| 能从 `st.secrets` 读密钥 | ✅ 已实现并测试（`tests/test_secrets.py`） |
| 不依赖本机文件 | ✅ 只有可选的 `~/.dsh/.credentials.yaml` 兜底，缺失即降级 |

### 部署步骤

1. 打开 <https://share.streamlit.io>
2. 用 **GitHub 账号**登录（授权读自己的仓库）
3. 点 **Create app** → 选 **Deploy a public app from GitHub**
4. 填三项：

   | 字段 | 值 |
   |---|---|
   | Repository | `cslkkl/code-requiem` |
   | Branch | `main` |
   | Main file path | `app.py` |

5. 点 **Deploy**，等 2-4 分钟构建。

### 部署后必须做：配 Secrets

进 app 页面 → 右下 **Settings** → **Secrets**，粘贴：

```toml
GITHUB_TOKEN = "ghp_你的只读token"
```

**为什么必须配**：GitHub 匿名 API 限流 60 次/小时，一次分析要 6-14 次请求。
不配的话**第 5 个访客就会看到限流错误**。配上后是 5000 次/小时。

拿 token：<https://github.com/settings/tokens> → Generate new token (classic)
→ 只勾 `public_repo` → 生成。它只需要读权限。

### 可选：配 AI 叙事

不配也能用，叙事会降级成结构化 Markdown（不白屏、不报错，界面标「AI 叙事：离线」）。
想开 AI，在 Secrets 里补：

```toml
LLM_BASE_URL = "https://api.deepseek.com/v1"
LLM_API_KEY = "sk-你的key"
LLM_MODEL = "deepseek-chat"
```

或者用 Hugging Face Router：

```toml
HF_TOKEN = "hf_你的token"
```

优先级：`LLM_*` > `HF_TOKEN` > 本机开发网关（线上无效）。

### 得到的地址

形如 `https://cslkkl-code-requiem-app-xxxxx.streamlit.app`。

把它填进：
- `docs/DEVPOST.md` 的 `[HF_SPACE_URL]`（2 处）
- `README.md` 顶部在线体验链接
- `AGENTS.md` 待办

---

## 方案 B：HF Spaces（需要 PRO 订阅）

订阅 Pro 后（$9/月），把 `scripts/deploy_hf.py` 里的 SDK 改成 `docker`，
并加一个 `Dockerfile`：

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
```

然后：

```powershell
$env:HF_TOKEN = "hf_xxx"
$env:GITHUB_TOKEN = gh auth token
.venv\Scripts\python.exe -u scripts\deploy_hf.py
```

**注意**：Docker 基础镜像默认不含 `git`。本项目的 `src/net.py::git_binary_available()`
会在没有 git 时自动走 GitHub REST API 兜底，功能不受影响；但装上 git 能让分析快一倍。

---

## 方案 C：Render / Railway / Fly.io

都支持 Docker，免费额度较小。用方案 B 的 `Dockerfile` 即可。
注意免费实例会在闲置后休眠，首次访问要等 30 秒冷启动 —— 评委体验不好，不推荐。

---

## 上线后自检清单

部署完逐项点一遍：

- [ ] 页面能打开，不是 502 / 构建失败
- [ ] 贴 `https://github.com/kornia/kornia` → 出分数、模式、三个问题
- [ ] 贴 `https://github.com/livid/v2ex-gae` → 判为「已故」
- [ ] 侧栏 AI 徽标状态符合预期（配了就是「在线」）
- [ ] 分析耗时 < 90 秒（首屏会显示实际秒数）
- [ ] 同一个仓库跑两次，分数一致
- [ ] 跑 5 个不同仓库不出现「限流」提示（= `GITHUB_TOKEN` 已生效）

---

## 常见问题

**构建失败：`ModuleNotFoundError`**
→ `requirements.txt` 里锁的版本可能在 Linux 上缺依赖。看 Streamlit Cloud 的
build log，缺什么补什么。本项目已锁 4 个直接依赖 + 6 个传递依赖。

**一直转圈或提示「网络太慢」**
→ 分析有 75 秒全局预算，超时会跳过低优先级步骤并给出结论。
   若频繁出现，是云厂商到 GitHub 的出网慢，重试即可。

**提示「限流」**
→ `GITHUB_TOKEN` 没配上，或配错了。去 Settings → Secrets 检查。

**页面样式全乱了**
→ `.streamlit/config.toml` 必须一起上传（Streamlit Cloud 会读它）。
   检查仓库里这个文件在不在。

**改了代码怎么更新**
→ Streamlit Cloud 默认监听仓库的 `main` 分支，push 后自动重新构建。
   不需要手动重新部署。
