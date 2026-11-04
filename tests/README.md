# tests/ — 测试手册

## 覆盖范围

| 文件 | 覆盖什么 | 不需要网络 |
|---|---|---|
| `test_scoring.py` | SPEC 第 3 章全部阈值表、bus factor 参考实现、六维满分之和 = 100 | ✅ 纯函数 |
| `test_boundary.py` | SPEC 第 5 章边界：空仓库、单 commit、非 Git 目录、坏地址、schema 完整性、确定性 | ✅ 用本地临时仓库 |

## 运行方式

```powershell
# 全量
.venv\Scripts\python.exe -u -m unittest discover -s tests -v

# 只跑打分
.venv\Scripts\python.exe -u -m unittest discover -s tests -p "test_scoring.py"

# 只跑边界
.venv\Scripts\python.exe -u -m unittest discover -s tests -p "test_boundary.py"
```

## 真实仓库验收

不在 unittest 里（会依赖网络和 GitHub 配额），单独跑：

```powershell
.venv\Scripts\python.exe -u scripts\verify_live.py
```

它跑三个仓库、验证三种模式、并把结果写进 `docs/verification.json`。

## 测试约定

- 用 `unittest`，不引入 pytest（HF Spaces 部署不需要测试依赖）
- 不 mock 网络：需要网络的测试放 `scripts/` 里显式跑，不放 unittest
- 每条 SPEC 阈值都要有边界值断言（`7/8`、`180/179`、`730/729` 这种）
- 新增打分维度必须同时加 `test_scoring.py` 的边界断言和 `TestMaxTotal` 的和校验
