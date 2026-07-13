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
