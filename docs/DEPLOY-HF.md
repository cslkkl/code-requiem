# 部署到 Hugging Face Spaces

> 这份文档是给「手上只有 token，不知道下一步点什么」的人写的。
> 全部步骤加起来约 5 分钟，其中 3 分钟是等 Space 构建。

## 一、你需要准备什么

只需要一样东西：**一个 Hugging Face 的 Write token**。

| 项目 | 说明 |
|---|---|
| 权限类型 | **Write**（不是 Read）—— 建 Space 属于写操作 |
| 免费吗 | 免费，注册就有 |
| 有效期 | 建 token 时选 "No expiration" 最省事 |

### 拿到 token 的三步

1. 打开 <https://huggingface.co/settings/tokens>（没账号先注册，邮箱即可）
2. 点 **New token** → Name 填 `code-requiem` → Role 选 **Write** → Create
3. 复制那串 `hf_` 开头的内容（**只显示一次**，关掉就看不到了）

## 二、执行部署

在项目根目录打开 PowerShell：

```powershell
cd "C:\Users\11965\Desktop\代码安魂曲"

# 1. 配好两个 token
$env:HF_TOKEN = "hf_你刚才复制的"
$env:GITHUB_TOKEN = gh auth token      # 强烈建议带上，理由见下方「为什么必须配 GITHUB_TOKEN」

# 2. 先做前置检查（不联网建东西，只验证 token 和打包内容）
.venv\Scripts\python.exe -u scripts\deploy_hf.py --check

# 3. 真的部署
.venv\Scripts\python.exe -u scripts\deploy_hf.py
```

看到这样的输出就是成功了：

```
=== 完成 ===
  Space 页面: https://huggingface.co/spaces/<你的用户名>/code-requiem
  应用地址:   https://<你的用户名>-code-requiem.hf.space
  首次构建约 2-4 分钟，看页面上的 Building 状态。
```

## 三、部署脚本做了什么

1. **在前置检查阶段**验证 token 有效性，并打印登录的用户名
2. **在临时目录拼打包内容**：只放运行必需的 20 个文件（`app.py`、`src/`、
   `requirements.txt`、`.streamlit/`），测试、标定数据、截图一律不上传
3. **生成 Space 专用 README**：HF 靠 README 顶部的 YAML frontmatter 识别 SDK，
   所以脚本单独写一份带 frontmatter 的 README，**不动主 README**
   （否则 GitHub 页面顶部会多出一块元数据表格）
4. **上传前扫描密钥**：命中 `hf_` / `sk-` / `ghp_` 就中止，不把密钥推到公开仓库
5. **创建 Space**（已存在则复用）
6. **写入 Secrets**：`GITHUB_TOKEN` 与 `HF_TOKEN` 直接进 Space 的环境变量，
   不进文件树
7. **上传文件并提交**

## 四、为什么必须配 GITHUB_TOKEN

GitHub 匿名 API 限流是 **60 次/小时**，而我们分析一个仓库要 6-14 次请求。
也就是说：**第 5 个访客就会看到「限流」错误**。

配上 token 后是 **5000 次/小时**，够几百人正常使用。

这个 token 只需 `public_repo` 只读权限，不需要任何写权限。

## 五、部署后必做的一件事

HF 的 AI 叙事走 Hugging Face Router（模型 `Qwen/Qwen2.5-7B-Instruct`），
用的是同一个 `HF_TOKEN`。脚本已经把它写成 Space Secret 了。

但如果免费额度用完或 Router 不可用，**叙事会自动降级为结构化模板**——
不会白屏，不会报错，只是文字没那么生动。界面上会明确标出「AI 叙事：离线」。

想换成别的模型，在 Space 的 **Settings → Variables and secrets** 里加：

| 变量名 | 作用 |
|---|---|
| `LLM_BASE_URL` | 任意 OpenAI 兼容端点，如 `https://api.deepseek.com/v1` |
| `LLM_API_KEY` | 该端点的 key |
| `LLM_MODEL` | 模型名 |

优先级：`LLM_*` > `HF_TOKEN` > 本地网关（只在开发机有效）。

## 六、常见问题

**构建失败，日志说 `ModuleNotFoundError`**
→ `requirements.txt` 是本地锁定的版本，个别包在 Linux 上可能有差异。
   在 Space 页面右上角 **Files** 里直接看 build log，缺什么补到 `requirements.txt`。

**打开后一直转圈，或提示「网络太慢」**
→ 分析有 75 秒全局预算，超时会跳过低优先级步骤。若频繁出现，
   说明 Space 到 GitHub 的出网慢，属于平台侧问题，重试即可。

**提示「限流」**
→ `GITHUB_TOKEN` 没配上。去 **Settings → Variables and secrets** 检查。

**想删掉重来**
→ 在 HF 上删掉那个 Space，重新执行部署脚本即可（脚本是幂等的）。

## 七、也可以用网页手动传

不想跑脚本的话：

1. <https://huggingface.co/new-space> → Space name 填 `code-requiem` →
   SDK 选 **Streamlit** → 创建
2. 进 Space 的 **Files** 标签 → **Add file** → 上传 `app.py`、`requirements.txt`
3. 用 **Add file → Create file** 建 `src/` 下的每个文件（麻烦，不推荐）
4. 编辑 Space 的 README.md，在顶部加上 `scripts/deploy_hf.py` 里 `SPACE_README`
   那段 frontmatter
5. **Settings → Variables and secrets** 加 `GITHUB_TOKEN` 和 `HF_TOKEN`

手动传 20 个文件容易漏，建议还是跑脚本。

## 八、部署完成后要回填的地方

部署成功后，把应用地址填进这些位置：

| 文件 | 占位符 |
|---|---|
| `docs/DEVPOST.md` | `[HF_SPACE_URL]`（2 处） |
| `README.md` | 顶部的在线体验链接 |
| `AGENTS.md` | 待办里的 HF 部署项 |
