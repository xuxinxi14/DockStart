# DockStart v0.10.0 Assisted Stable

DockStart 是基于 AutoDock Vina 的第三方中文分子对接工作台。本版本将既有最小对接闭环
整理为可重复、离线的 Windows GUI，并提供两个隔离 profile：Basic Stable 面向已有 PDBQT
的用户；Assisted Stable 额外随附独立、可替换的 RDKit/Meeko 准备工具链。

## 本版本包含

- AutoDock Vina 1.2.7、本地项目、对接箱体/Vina 参数、运行、结果表格和 Markdown 报告；
- 暗色/亮色主题与按需加载的 3Dmol 结构工作台；
- 固定后台任务队列、页面重开任务重连、工具链 hash 缓存与显式重检；
- 原子项目写入、schema migration、revision 冲突检测、崩溃 run 恢复；
- 输入、输出、配置、日志和工具二进制 SHA256 provenance；
- Assisted Stable 中的 CPython 3.11.15、RDKit 2026.3.3、Meeko 0.7.1 及固定依赖；
- Meeko、Gemmi、tqdm 对应版本源码、第三方许可证和来源/SHA256 清单。

## 开箱即用边界

- Basic：导入准备好的 receptor/ligand PDBQT 后可离线完成真实 Vina 流程；
- Assisted：可离线从 PDB 受体与 SDF/MOL 配体尝试准备 PDBQT，再进入 Vina 流程；
- 用户配置的兼容 Python 工具链仍优先；bundled runtime 是离线 fallback；
- Meeko 保持普通 `site-packages/meeko/` 目录，不冻结进 DockStart 可执行文件。

自动准备结果仍需人工检查质子化、电荷、构象、缺失残基、水、金属、辅因子和链选择。
Docking score 仅供结构结合趋势参考，不能替代实验验证，也不能证明药效。

## 未包含

- Open Babel、MGLTools、PLIP、ProLIF；
- MOL2/SMILES 自动准备、复杂受体结构修复；
- 相互作用分析、pocket prediction、分子动力学、批量虚拟筛选、AI 药效预测；
- AutoDock Vina 算法或 scoring function 修改；
- 运行时联网安装 scientific packages。

## 发布门禁

本候选必须同时通过：

1. 白名单 development stage 的真实准备 + Vina + 解析 + 报告流程；
2. Tauri post-package 目录的同一流程；
3. NSIS 真实安装目录的同一流程，以及静默卸载无残留；
4. Python、Rust、前端测试与实际 GUI 流程验收。

只有 `.release/assisted/artifact-manifest.json` 中 `publishable=true` 的产物可上传。

## Windows x64 产物

> 最终发布前用 `docs/release/v0_10_0_build_report.md` 的实测值替换占位符。

| 文件 | 大小 | SHA256 |
| --- | ---: | --- |
| `DockStart_0.10.0_Assisted_x64_en-US.msi` | `{{MSI_SIZE_BYTES}}` | `{{MSI_SHA256}}` |
| `DockStart_0.10.0_Assisted_x64-setup.exe` | `{{NSIS_SIZE_BYTES}}` | `{{NSIS_SHA256}}` |

DockStart 自有代码采用 Apache-2.0；安装包中的第三方组件遵循各自许可证。Meeko 0.7.1
以 LGPL-2.1 条款作为独立可替换组件分发。详见安装包内
`resources/licenses/THIRD_PARTY_NOTICES.md`。以上为工程合规说明，不构成法律意见。
