# 自托管字体

界面使用 **Geist** 与 **Geist Mono** 可变字体，以 WOFF2 放在本目录。
`app.py` 启动时把文件编码成 data URL 注入 `@font-face`，运行时不访问字体 CDN。

| 文件 | 用途 |
|---|---|
| `geist-latin-wght-normal.woff2` | 英文与界面正文 |
| `geist-mono-latin-wght-normal.woff2` | 数字、仓库名、代码与运行数据 |

中文字体按平台回退到苹方、微软雅黑、Noto Sans CJK 或文泉驿。
字重使用可变范围，标题与正文只靠字重和字号区分。

## 许可

两个字体均使用 SIL Open Font License 1.1。
许可证原文随字体一起保留：

- [GEIST-LICENSE.txt](GEIST-LICENSE.txt)
- [GEIST-MONO-LICENSE.txt](GEIST-MONO-LICENSE.txt)

来源：Fontsource 的 `@fontsource-variable/geist` 与
`@fontsource-variable/geist-mono` 包。
