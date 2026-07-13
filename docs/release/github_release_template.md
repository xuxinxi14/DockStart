# DockStart v0.10.0 Assisted Stable

DockStart 是基于 AutoDock Vina 的第三方中文分子对接工作台。v0.10.0 将现有最小对接闭环整理为可复现、离线的 Windows GUI，并提供两个隔离的发布 profile：Basic Stable 面向已有 PDBQT 的用户；Assisted Stable 额外随附独立、可替换的 RDKit/Meeko 准备工具链。

## 本版本包含

- AutoDock Vina 1.2.7、本地项目、对接箱体、Vina 参数、运行、结果表格和 Markdown 报告；
- 暗色/亮色主题，以及按需加载的 3Dmol 结构工作台；
- 固定后台任务队列、页面重开任务重连、工具链运行时 hash 缓存与显式重检；
- 原子项目写入、schema migration、revision 冲突检测和未完成 run 恢复；
- 输入、输出、配置、日志和工具二进制的 SHA256 provenance；
- Assisted Stable 随附 CPython 3.11.15、RDKit 2026.3.3、Meeko 0.7.1 及固定依赖；
- Meeko、Gemmi、tqdm 对应版本的源码包、第三方许可证和来源/SHA256 清单。

## 开箱即用边界

- Basic：导入准备好的 receptor/ligand PDBQT 后，可离线完成真实 Vina 流程；
- Assisted：可离线从 PDB 受体与 SDF/MOL 配体尝试准备 PDBQT，再进入 Vina 流程；
- 用户配置的兼容 Python 工具链仍优先，随附 runtime 是离线 fallback；
- Meeko 保持普通 `Lib/site-packages/meeko/` 目录，不冻结进 DockStart 可执行文件。

自动准备结果仍需人工检查质子化、电荷、构象、缺失残基、水、金属、辅因子和链选择。Docking score 仅供结构结合趋势参考，不能替代实验验证，也不能证明药效。

## 未包含

- Open Babel、MGLTools、PLIP、ProLIF；
- MOL2/SMILES 自动准备和复杂受体结构修复；
- 相互作用分析、口袋预测、分子动力学、批量虚拟筛选、AI 药效预测；
- AutoDock Vina 算法或 scoring function 修改；
- 运行时联网安装 scientific packages。

## 发布门禁

本候选已同时通过：

1. development stage 的真实准备、Vina、解析与报告流程；
2. Tauri post-package 目录的同一流程；
3. NSIS 真实安装目录的同一流程及静默卸载无残留；
4. Python、Rust、前端构建与实际 GUI 验收。

`.release/assisted/artifact-manifest.json` 已记录 `release_status=passed` 与 `publishable=true`。

## Windows x64 产物

| 文件 | 大小 | SHA256 |
| --- | ---: | --- |
| `DockStart_0.10.0_Assisted_x64_en-US.msi` | 113,116,382 bytes | `40a38be1ec6ff531d8c4d5c89df2d3cfed53ae5d20abfc24e5376e404f40b5bd` |
| `DockStart_0.10.0_Assisted_x64-setup.exe` | 73,167,840 bytes | `9920561060a15c0b8e708bab68cf6214d6b5d3815ca868f5a1ffcad900383e0a` |

建议 Windows 用户优先使用 NSIS `setup.exe`。两个安装包尚未进行 Authenticode 签名，Windows SmartScreen 或企业策略可能显示“未知发布者”。

DockStart 自有代码采用 Apache-2.0；安装包中的第三方组件遵循各自许可证。Meeko 0.7.1 以 LGPL-2.1 条款作为独立可替换组件分发，详见安装包内 `resources/licenses/THIRD_PARTY_NOTICES.md`。以上为工程合规说明，不构成法律意见。
