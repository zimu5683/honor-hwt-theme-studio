# DESIGN.md 设计参考

本项目已引入 [VoltAgent/awesome-design-md](https://github.com/VoltAgent/awesome-design-md) 的设计文档集合，原始内容位于：

- `docs/design-md/awesome-design-md/`
- 每个品牌目录包含一个 `DESIGN.md` 和一个 `README.md`。

## 当前项目默认设计基线

根目录的 `DESIGN.md` 采用 IBM Carbon 风格作为桌面编辑器的默认参考：结构清晰、数据密集、蓝色主色、适合 PySide6 工具型应用。

如果需要切换风格，可将任意品牌目录下的 `DESIGN.md` 复制到项目根目录，覆盖当前设计基线。例如：

```powershell
Copy-Item docs/design-md/awesome-design-md/apple/DESIGN.md DESIGN.md -Force
```

本目录内容来自上游仓库并保留原始 MIT 许可；本项目的实现仍以 Qt/QSS 可实现的控件和交互为准。
