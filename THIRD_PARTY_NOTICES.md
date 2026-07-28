# Third-party notices

DockStart 自有代码的许可证见仓库 `LICENSE`。发布包中的第三方组件分别遵守其自身许可证，
不能把整个安装包理解为只有一种许可证。

- Basic Stable 的发布 notice 模板：`resources/licenses/THIRD_PARTY_NOTICES.md`。
- Assisted Stable 的发布 notice 模板：`resources/assisted/THIRD_PARTY_NOTICES.md`。
- 固定 artifact、版本、官方来源与 SHA256：
  `resources/assisted/SOURCE_MANIFEST.json`。
- 完整工程合规记录：`docs/license_notes.md`。

Assisted Stable 将 Meeko 作为独立、可替换的 Python 组件分发，不把它冻结进 DockStart
可执行文件。Meeko 0.7.1、Gemmi 0.7.5 和 tqdm 4.67.1 的同版本官方 source archive
会随 Assisted 安装资源提供。DockStart 没有修改这些上游包。

桌面端直接使用 serde/serde_json 生成结构化后台任务事件；两者按 MIT 条款分发，许可证
文本见 `resources/licenses/Serde_LICENSE-MIT.txt`。

AutoGrid4 不属于 DockStart 安装包内容。v0.12.0 只允许用户配置其自行安装的
`autogrid4.exe`；该上游工具按 GNU GPL 提供，DockStart 不重新分发其二进制。

当前源码工作树中的 AD4Zn beta 还需要用户自行提供 `AD4Zn.dat`。该文件在上游文件头中
声明 GPL-2.0-or-later，DockStart 不把它提交到仓库，也不随 Basic/Assisted 安装包分发。
用户明确选择文件后，DockStart 会把它复制到用户项目及后续 run，用于可复现性校验，
并记录本机来源路径、SHA256、GPL-2.0-or-later、受支持参数配置和固定上游参考。固定参考
为 AutoDock Vina v1.2.7 的 `data/AD4Zn.dat`；分享含该副本的项目时，分享者需要自行履行
GPL 再分发义务。当前 v0.12.0 Release 不包含 AD4Zn beta。
