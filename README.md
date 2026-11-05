# 代码安魂曲 · Code Requiem

> 输入任意公开 Git 仓库地址 → 用**纯确定性算法**打出 0–100 健康分 → 让 AI 把它写成一份**能直接贴进交接单**的文档。

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.66-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![Live Demo](https://img.shields.io/badge/Live%20Demo-Streamlit-FF4B4B?logo=streamlit&logoColor=white)](https://code-requiem-iczezzcmbgqssvxkaciarxe.streamlit.app)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

**在线试用** → <https://code-requiem-iczezzcmbgqssvxkaciarxe.streamlit.app>

> 打开即用，不需要注册、不需要粘贴任何密钥。第一次分析约 10-60 秒。

**这是给「接手别人代码」的人用的工具，不是给怀念项目的人用的玩具。**

打分是确定的、可复现的、可以写进风险报告里的；叙事是 AI 写的，只是为了让这份风险报告有人愿意读完。

---

## 目录

- [30 秒看懂](#30-秒看懂)
- [为什么需要它](#为什么需要它)
- [快速开始](#快速开始)
- [配置 LLM（三种方式）](#配置-llm三种方式)
- [架构：分析层不含 AI](#架构分析层不含-ai)
- [评分维度与权重](#评分维度与权重)
- [三种输出模式](#三种输出模式)
- [边界处理清单](#边界处理清单)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [第三方依赖与来源声明](#第三方依赖与来源声明)
- [界面截图](#界面截图)
- [已知限制](#已知限制)
- [Roadmap](#roadmap)
- [许可证](#许可证)

---

## 30 秒看懂

```
                     ┌───────────────────────────────────────────────┐
   仓库地址  ──────►  │ ① 采集    git log · 文件树 · registry 公开 API │
 github.com/a/b      └───────────────────────┬───────────────────────┘
                                             ▼
                     ┌───────────────────────────────────────────────┐
                     │ ② 打分    纯确定性 · 零 LLM · 零随机 · 可复现    │
                     │    6 维加权 → 0–100 → 三态判定                 │
                     └───────────────────────┬───────────────────────┘
                        ┌────────────────────┼────────────────────┐
                     ≥70▼               30–69▼                  <30▼
                  「代码遗嘱」        「临终关怀」            「项目悼词」
                  交接必读            遗嘱 + 悼词叠加        死因复盘
                        └────────────────────┼────────────────────┘
                                             ▼
                     ┌───────────────────────────────────────────────┐
                     │ ③ 叙事    Qwen2.5-7B-Instruct 把数字写成人话    │
                     │    只改措辞，不改分数；挂了就降级输出指标表      │
                     └───────────────────────┬───────────────────────┘
                                             ▼
                          Markdown 文档（可复制 · 可存档 · 可粘进 PR）
```

三个动作，一个输入框：**贴地址 → 看分数 → 拿走文档。**

---

## 为什么需要它

真实场景：你接手了一个三年前的项目，原作者已经离职。

你现在需要回答三个问题，但没人能回答你：

| 你要问的问题 | Code Requiem 给你的答案 |
|---|---|
| 这块代码还能不能救？ | 一个 0–100 的分数，六个维度拆开告诉你死在哪一维 |
| 我动了哪一块会炸？ | 「雷区」章节列出长期未改的核心文件与 FIXME/HACK 具体行号 |
| 如果关键人明天走了会怎样？ | **bus factor**：一个人扛了 60% 以上的提交，等于项目只有一条命 |

**价值主张是工程交接与 bus factor 风险盘点。** 悼词那个形式只是让人愿意点开、愿意转发——钩子在外，交付物在内。

目标用户：

- 接手别人代码的工程师（想知道先看哪里）
- 技术负责人（想在复盘会上把风险量化成数字）
- 要算单点风险的管理者（想把「只有小王会」变成一个可上报的指标）

---

## 快速开始

### 环境要求

- Windows / macOS / Linux
- Python **3.10+**（本项目在 Python 3.12 上实测）
- 已安装 [`uv`](https://docs.astral.sh/uv/)（比 pip 快一个数量级，推荐）
- 系统里要有 `git` 可执行文件（本项目用 `git clone --depth` 浅克隆）

### Windows PowerShell — 完整可复制流程

```powershell
# 1. 进项目目录
cd C:\Users\11965\Desktop\代码安魂曲

# 2. 建虚拟环境（固定 3.12）
uv venv --python 3.12 .venv

# 3. 切到清华镜像装依赖（国内必做，否则 Hugging Face 相关包会卡很久）
$env:UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
uv pip install --python .venv\Scripts\python.exe -r requirements.txt

# 4. 启动界面
.venv\Scripts\python.exe -u -m streamlit run app.py
```

浏览器会自动打开 `http://localhost:8501`。没自动开就手动访问。

### macOS / Linux

```bash
cd 代码安魂曲
uv venv --python 3.12 .venv
export UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -u -m streamlit run app.py
```

### 不装 uv 的退路

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv\Scripts\python.exe -u -m streamlit run app.py
```

### 最少要贴什么进去

任何**公开可 `git clone`** 的地址都行：

- `https://github.com/kornia/kornia`
- `https://github.com/cslkkl/code-requiem`
- `https://gitlab.com/...`（非 GitHub 也能分析，只是 issue 那一项按最低分计）
- 本地路径也行（`D:\work\your-repo`），只要有 `.git` 目录

---

## 配置 LLM（三种方式）

分析层不需要任何配置就能跑。**LLM 只是最后一步的润色，不配也能出结果，只是文档变成纯指标表。**

### 方式一：Hugging Face Token（部署在 Spaces 时用这个）

1. 打开 <https://huggingface.co/settings/tokens>
2. 点 **Create new token**，类型选 **Read** 就够（不要给 write 权限）
3. 复制生成的 `hf_...` 字符串
4. 填进 HF Spaces：**Settings → Variables and secrets → New secret**
   - **Name**：`HF_TOKEN`
   - **Value**：你的 `hf_...`
5. Space 会自动重启，顶部徽标变成绿色即配置成功

本地开发同理，用环境变量：

```powershell
$env:HF_TOKEN="<your-hf-token>"
```

走这条路时，模型默认是 `Qwen/Qwen2.5-7B-Instruct`，端点是 `https://router.huggingface.co/v1`。

### 方式二：自定义 OpenAI 兼容网关

任何兼容 `/chat/completions` 的服务都能接（自建 vLLM、OpenRouter、各类中转）：

```powershell
$env:LLM_BASE_URL="https://your-gateway.example.com/v1"
$env:LLM_API_KEY="<your-token>"
$env:LLM_MODEL="your-model-name"
```

三个变量同时给出时**优先级高于** `HF_TOKEN`。

### 方式三：什么都不配（降级模式）

直接启动，不设任何变量。行为：

- 打分**完全正常**，六维分数、bus factor、雷区列表一条不少
- 叙事章节由确定性模板从指标直接渲染
- 页面顶部出现提示条：`⚠️ AI 叙事生成不可用，以下是结构化指标。`

这是刻意设计：**LLM 挂了整个应用不许不可用。**

---

## 架构：分析层不含 AI

> 这一节是设计红线，也是本项目的技术立场。

```
app.py（Streamlit 界面）
   │
   ├─► src/analyzer.py ── 编排：clone → 采集 → 打分 → 组装 schema
   │      ├─► src/git_metrics.py   git log / 文件树 / README / FIXME 扫描
   │      ├─► src/deps.py          PyPI · npm registry 查询最后发布时间
   │      ├─► src/issues.py        GitHub Issues API 响应中位数
   │      └─► src/scoring.py       纯函数：输入数字 → 输出数字
   │
   └─► src/narrative.py ── 只做一件事：把 metrics 写成 Markdown
          └─► src/llm.py           HF Inference / 任意 OpenAI 兼容网关
```

**明确声明：**

1. `src/scoring.py` 里没有 `import requests`、没有 `import random`、没有 `datetime.now()` 之外的任何外部状态。它是**纯函数集合**：同一个输入必然得到同一个输出。
2. `src/git_metrics.py` 只读 git 仓库和本地文件，不发网络请求。
3. **LLM 永远拿不到打分权。** 它收到的是一份已经算好的 JSON 指标，任务是"把这份 JSON 写成人话"。它无法让一个死仓库变成活的，也无法让一个 1 分的维度变成 25 分。
4. 同一个仓库、同一时刻、连跑两次，`deterministic_key` 与 `total` 必须完全一致。这是验收项，不是承诺。

换句话说：**AI 是排版工，不是裁判。**

---

## 评分维度与权重

满分 100，扣分制，六维相加。阈值全部来自 [`docs/SPEC.md` 第 3 章](docs/SPEC.md)，实现见 `src/scoring.py`。

| # | 维度 | 权重 | 数据来源 | 判定依据（SPEC 3.x） |
|---|---|---:|---|---|
| 1 | 最后提交距今 | **30** | `git log` 默认分支 committer date | 0–7 天得 30；8–30 得 25；31–90 得 18；91–180 得 10；181–365 得 5；>365 得 0 |
| 2 | Bus Factor | **25** | `git log` 按 author email 去重计数 | 累计提交占比首次 ≥ **60%** 时数到的人头数。1 → 0 分；2 → 10；3 → 17；≥4 → 25 |
| 3 | 提交活跃度趋势 | **15** | 近 6 个月 vs 前 6 个月提交数 | ratio ≥1.0 得 15；0.5–0.99 得 10；0.1–0.49 得 5；<0.1 得 0；历史不足 12 个月按 10 分 |
| 4 | 依赖健康度 | **15** | PyPI `/pypi/{pkg}/json`、npm `registry.npmjs.org` | 取**最老**依赖距今：<180 天 15；180–364 天 10；365–729 天 5；≥730 天 0；无清单/超时按中性 8 |
| 5 | Issue 响应 | **10** | GitHub `/repos/{o}/{r}/issues?state=all` | 已关闭 issue 的 `closed_at - created_at` 中位数：<7 天 10；7–29 天 6；30–89 天 3；≥90 天 1；取不到 1 |
| 6 | 文档完整度 | **5** | 仓库内 README 与关键词扫描 | README + 安装说明 + 代码示例 5；仅 README（≥500 字符）3；README 过短 1；无 README 0 |
| | **合计** | **100** | | |

**为什么 bus factor 用 60% 而不是 80%**：60% 更早告警。一个人占六成提交就已经是单点，等到八成时项目已经死了。

**为什么依赖取最老的那个**：链条强度由最弱环节决定。99 个新依赖救不了 1 个两年没更新的。

---

## 三种输出模式

| 健康分 | 状态 | 输出 | 什么时候看到 |
|---:|---|---|---|
| **≥ 70** | 存活 `alive` | **代码遗嘱**（5 章） | 活跃维护的库，你要接手但风险可控 |
| **30 – 69** | 承压 `critical` | **临终关怀**：完整遗嘱 + 分隔线 + 完整悼词 | 还在动但明显衰减，bus factor 常见为 1 |
| **< 30** | 已故 `dead` | **项目悼词**（5 章） | 两年没提交、依赖全过期，你要评估"重写还是抢救" |

遗嘱五章：`我留下了什么` / `谁在养我` / `雷区：千万别动` / `接手的人按这个顺序做` / `最后的话`。

悼词五章：`它的一生` / `死因`（直接原因·根本原因·加速因素）/ `它留下了什么` / `教训` / `结语`。

`bus factor = 1` 时，第二章会硬性写出这句话：**「这个人离开，项目即终止」**。

---

## 边界处理清单

来自 [`docs/SPEC.md` 第 5 章](docs/SPEC.md)。原则：**任何输入都必须有输出，不允许抛出未捕获异常。**

| # | 情况 | 处理 |
|---|---|---|
| 1 | 仓库不存在 / 私有 | 明确报错：「无法访问该仓库，请确认它是公开的」 |
| 2 | 只有 1 个 commit | 正常分析；bus factor 强制为 1；趋势按 10 分 |
| 3 | 空仓库（0 commit） | 返回：「这个项目还没有出生」 |
| 4 | 仓库 > 500MB 或 commit > 50000 | 只分析最近 2000 个 commit，结果里标注「已采样」 |
| 5 | 无 README | 文档分 0，不报错 |
| 6 | 依赖 API 超时（5 秒） | 按中性 8 分，标注「依赖信息未获取」 |
| 7 | 非 GitHub 仓库（GitLab / 本地） | 有 git log 就分析；issue 项按 1 分 |

---

## 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.10+ | 实测 3.12 |
| Git 解析 | `GitPython` + `git clone --depth` | 浅克隆，200 层历史足够算指标，速度快一个量级 |
| 界面 | **Streamlit** | 单页，输入框 + 结果渲染，不需要前端构建 |
| LLM | Hugging Face Inference API · `Qwen/Qwen2.5-7B-Instruct` | 免费额度，只负责叙事 |
| 部署 | **Hugging Face Spaces**（Streamlit SDK） | 公开链接，陌生人可直接打开 |
| 运行依赖 | `gitpython` · `streamlit` · `huggingface_hub` · `requests` | 锁定版本见 [`requirements.txt`](requirements.txt) |

为什么不上 React / FastAPI：这不是一个需要"产品化"的项目，这是一个人就能读完的一次性分析。Streamlit 一天能跑通全流程，把时间留给算法正确性。

---

## 项目结构

```
代码安魂曲/
├── app.py                    # Streamlit 入口：输入框、结果渲染、示例按钮
├── requirements.txt          # 运行依赖（锁定版本，不含测试依赖）
├── README.md
├── docs/
│   ├── SPEC.md               # 实现规格：算法阈值、输出结构、边界处理
│   ├── CONTRACT.md           # 模块契约：函数签名与 schema（并行开发的唯一事实来源）
│   ├── DEVPOST.md            # 黑客松七项提交材料（可复制粘贴版）
│   ├── DEMO.md               # 60–90 秒演示分镜脚本
│   └── screenshots/          # 界面截图
├── src/
│   ├── analyzer.py           # 编排层：analyze_repo() 唯一公开入口
│   ├── git_metrics.py        # clone、commit 采集、LOC、README、FIXME 扫描
│   ├── scoring.py            # 六维打分（纯函数，无网络无随机）
│   ├── deps.py               # PyPI / npm registry 依赖年龄
│   ├── issues.py             # GitHub issue 响应中位数
│   ├── schema.py             # 结果 schema 与校验
│   ├── llm.py                # 三家 provider 优先级与降级
│   ├── narrative.py          # 指标 → Markdown（含降级模板）
│   ├── prompts.py            # 遗嘱 / 悼词 prompt
│   └── format.py             # 界面用的格式化工具
└── tests/                    # 验收测试（阈值表、确定性、边界）
```

---

## 第三方依赖与来源声明

| 依赖 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| [GitPython](https://github.com/gitpython-developers/GitPython) | 3.2.1 | BSD-3-Clause | 读取 git 历史与文件树 |
| [Streamlit](https://github.com/streamlit/streamlit) | 1.66.1 | Apache-2.0 | Web 界面 |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub) | 2.4.0 | Apache-2.0 | 调用 HF Inference API |
| [requests](https://github.com/psf/requests) | 2.34.2 | Apache-2.0 | PyPI / npm / GitHub API 请求 |
| [gitdb](https://github.com/gitpython-developers/gitdb) | 4.0.12 | BSD-3-Clause | GitPython 的底层对象库 |
| [smmap](https://github.com/gitpython-developers/smmap) | 5.0.3 | BSD-3-Clause | gitdb 的 mmap 实现 |

### 模型来源

本项目通过 **Hugging Face Inference API** 调用 **[`Qwen/Qwen2.5-7B-Instruct`](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct)**。

- 模型由 **Qwen 团队（阿里巴巴）** 开发并发布
- 许可证：**Apache-2.0**
- 调用方式：`https://router.huggingface.co/v1/chat/completions`，OpenAI 兼容协议
- 模型**只用于**把已算好的结构化指标写成叙事文本；不参与任何打分、判定或数据采集
- 本项目**不**在本地加载模型权重，不下载权重文件，只走推理 API

### 数据来源

- PyPI：<https://pypi.org/pypi/{package}/json>（公开 API）
- npm：<https://registry.npmjs.org/{package}>（公开 API）
- GitHub REST：<https://api.github.com>（未认证限速 60 次/小时，配置 `HF_TOKEN` 不提升此额度，只有 `GITHUB_TOKEN` 才有用）

### 安全声明

- 仓库内**不含**任何 API key、token、密码。所有密钥一律走环境变量或 HF Spaces Secrets。
- 克隆是浅克隆到临时目录，分析完成后即清理，不上传、不留存用户仓库内容。

---

## 界面截图

> **界面截图待补**：截图将放在 `docs/screenshots/main.png`。
> 在补上之前，可以本地运行 `streamlit run app.py` 直接看到界面。

---

## 已知限制

1. **未认证的 GitHub API 限速 60 次/小时**，短时间连续分析多个仓库会触发 403，此时 issue 维度降为最低分并在结果里标注。
2. **bus factor 依赖邮箱去重**：同一人用多个邮箱会被算成多个人，bus factor 偏高（偏乐观）。这是已知偏差，宁可乐观也不误伤。
3. **依赖分析只看清单**：间接依赖、lock 文件里的传递依赖不追。
4. **Go 项目跳过依赖维度**：没有稳定的公开 registry API，按中性分处理。
5. **活跃度趋势需要 12 个月历史**，新仓库该项按 10 分固定。
6. **超大型仓库**（>50000 commit）会采样最近 2000 个，分数是近似值，结果里会标注。
7. **叙事质量随模型波动**：Qwen2.5-7B 不是顶级模型，偶尔会写出干瘪的章节。分数永远准确，措辞不必当真。

---

## Roadmap

- [ ] `GITHUB_TOKEN` 支持，把 issue 维度从"限速即降级"救回来
- [ ] 依赖年龄支持 lock 文件（`poetry.lock` / `package-lock.json`）
- [ ] 历史分数曲线：同一仓库按周采样，画出"死亡过程"折线
- [ ] 导出 PDF / 直接生成 PR 描述的 Markdown 片段
- [ ] bus factor 用 `git blame` 加权（代码行归属比提交次数更接近真实维护权）
- [ ] 私有仓库支持（用户自带 token，纯本地分析）

---

## 许可证

本项目以 **MIT License** 发布。

```
MIT License

Copyright (c) 2026 Code Requiem contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

<p align="center">
  给还活着的项目写遗嘱，给已经死掉的项目写悼词。<br>
  <sub>Build Your First AI App · Devpost Hackathon · 2026</sub>
</p>
