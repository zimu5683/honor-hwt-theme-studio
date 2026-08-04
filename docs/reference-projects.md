# 后续参考项目

以下项目是大雪主题编辑器后续优化的长期参考来源。引用它们用于学习协议、格式处理、资源管理和工程安全边界；具体实现仍以本项目的 HWT 运行时行为、测试和兼容性要求为准。

## 最高优先级

- [Huawei-To-Honor-Theme-Converter](https://github.com/buergerr/Huawei-To-Honor-Theme-Converter)：重点研究华为主题到荣耀主题的转换映射、资源命名差异、XML/压缩包处理顺序和可复现转换报告。
- [LocalSend protocol](https://github.com/localsend/protocol)：重点研究局域网发现、身份与会话、能力协商、传输完整性、错误码和版本兼容策略，用于完善桌面端与 Android 助手协议。

## 主题与资源处理

- [MaterialFiles](https://github.com/zhanghai/MaterialFiles)：重点研究文件浏览、目录授权、批量操作、排序筛选和 SAF 失败恢复体验。
- [Huawei-Watchface-Extractor-Python](https://github.com/BlackHatDevX/Huawei-Watchface-Extractor-Python)：重点研究华为资源容器识别、提取边界、元数据保留和派生素材与原始素材隔离。
- [iconapk2hwt](https://github.com/azzhu/iconapk2hwt)：重点研究 APK 图标资源到 HWT 的映射、图片格式转换和批量资源命名。
- [magiZ](https://github.com/KhunHtetzNaing/magiZ)：重点研究 MagicOS/荣耀主题资源结构、模块组织和版本差异。

## 安全与工程质量

- [exarch](https://github.com/bug-ops/exarch)：重点研究输入边界、失败关闭、可观测错误和安全默认值。
- [safezip](https://github.com/barseghyanartur/safezip)：重点研究 ZIP 路径穿越、重复条目、压缩炸弹、CRC 和解压预算防护。

## 本项目的落地顺序

1. 先建立华为/荣耀主题格式差异的样本、映射表和逐步转换报告。
2. 再完善 LocalSend 式发现、配对、能力协商、断点/重试和完整性验证，但保持当前协议的明确兼容版本。
3. 将 SAF、资源目录、预览和批量替换工作流向文件管理器级别的可恢复操作推进。
4. 对每一项格式或安全改动保留原始样本、增加回归测试，并用有界读取、临时文件、原子提交和失败回滚保护。

## 已落地借鉴

- `Huawei-To-Honor-Theme-Converter`：已将其可复核的图标路径迁移规则落入 `HONOR_PATH_ALIASES`，覆盖 `com.huawei`、`com.hicloud`、天气、时钟和音乐特殊别名；映射后的目标仍经过本项目的重复目标冲突审计，不直接覆盖荣耀原生资源。
- `localsend/protocol`：已借鉴“元数据先于二进制传输”的阶段划分，加入可选 `transfer_prepare` 能力和 `/api/v1/transfers/{id}/prepare`；本项目保留 Bearer 配对、HWT SHA-256 和旧版 PUT 兼容，不照搬 LocalSend 的设备模型或端口约定。
- `safezip`：已将 ZIP 文件/目录前缀重叠和本地压缩数据区间物理重叠纳入外层与嵌套归档审计，发现 `icons` 与 `icons/...` 或多个中心目录条目指向同一数据区间时在读取内容前阻断。
- `Huawei-Watchface-Extractor-Python` / `magiZ`：源 HWT 扫描现在生成独立兼容性报告，保留可读取资源与原始警告；重复资源、图片扩展名错配、非标准 XML 不再与最终导出严格验证混为一谈。
