"""代码安魂曲的 Python 包。

为什么同时支持「包内导入」和「扁平导入」：
  * `streamlit run app.py` 与 HF Spaces 会以脚本方式加载，习惯 `from src.analyzer import ...`
  * `python src/analyzer.py` 直接跑命令行走的是脚本模式，`src/` 在 sys.path 上

两套入口都要能工作，所以各模块用 try/except 双导入。不要改成单一风格，
除非同时改掉 README、测试与 HF Spaces 的入口方式。
"""

__version__ = "1.0.0"
