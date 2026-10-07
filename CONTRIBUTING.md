# 参与 DockStart / Contributing

欢迎提交可复现的问题、教程改进、翻译和代码修改；中文与英文均可。先阅读 [项目说明](PROJECT.md)、[Coding Agent 指南](AGENTS.md) 和相关源码，按实际版本区分已发布能力与开发改动。

Bug reports, documentation, translations and code contributions are welcome in Chinese or English. Read [PROJECT.md](PROJECT.md), [AGENTS.md](AGENTS.md) and the relevant source before changing behavior. Start with a small, reviewable change.

## 问题与建议

通过 [Issue 入口](https://github.com/xuxinxi14/DockStart/issues/new/choose) 选择使用问题、功能建议或文档反馈。使用问题请写明版本、Basic/Assisted 或源码运行、系统与工具版本、具体步骤、期望结果、错误码和相关日志。只提交公开或经过处理的最小复现数据，分享前检查个人路径与研究数据。

Use the issue forms and include the version, profile, operating system, steps, expected behavior and relevant errors/logs. Use public or sanitized minimal examples. A question about a docking score should include the protocol and input/parameter context; the score alone cannot establish experimental binding or efficacy.

## 从源码运行

Windows 开发环境使用根目录 [.node-version](.node-version) 和 [rust-toolchain.toml](rust-toolchain.toml) 固定的 Node.js/Rust，以及 Python 3.11+。Tauri 的系统构建前置条件和本地工具资源装配见 [Windows 打包说明](docs/release/windows_packaging.md)。安装包随附资源不代表干净克隆已经具备完整工具链。

```powershell
git clone https://github.com/xuxinxi14/DockStart.git
cd DockStart\apps\desktop
npm ci
npm run tauri dev
```

For Windows development, use the pinned Node.js and Rust versions above and Python 3.11+. Install the Tauri system prerequisites and configure the scientific tools separately. The installer instructions do not substitute for source-build resource preparation.

## 目录用途

| 目录 | 用途 / Purpose |
| --- | --- |
| `apps/desktop/` | React 界面与 Tauri 壳 / UI and desktop shell |
| `backend/adapters/` | 外部工具检测与安全调用 / External-tool adapters |
| `backend/dockstart_core/` | 项目、准备、运行、结果与记录 / Workflow and project logic |
| `backend/tests/` | 单元、mock 与条件性科学验收测试 / Tests and conditional scientific checks |
| `resources/` | 发布输入、示例、工具链与许可证清单 / Release inputs and manifests |
| `docs/` | 用户说明、协议边界、发布与历史记录 / Documentation |
| `scripts/` | 检查、资源装配、打包与外部验收 / Verification and packaging |
| `test/DockStart/` | 已跟踪的历史运行布局；不作为当前源码或下载入口 / Historical runtime layout |

实际 docking 项目应保存在独立目录；`output/`、`video/`、runtime、安装包与开发缓存不作为源码提交。历史 `test/DockStart/` 的已跟踪文件仍保留，后续清理需要单独评估来源、许可证、引用与迁移；新增忽略规则不会移除已有文件。

## 检查与提交

在仓库根目录运行统一检查：

```powershell
.\scripts\check_all.ps1
```

该入口依次检查 Git 空白、Python 编译与测试、npm 审计、前端测试与构建、Rust 格式/check/test/clippy。`-RequireReleaseResources` 要求实际发布资源，发布验收还需 [发布检查表](docs/release/release_checklist.md)。

只改文档时，核对 Markdown 路径与图片、下载链接和文件身份；修改 YAML/JSON 时检查语法和对应格式契约。代码改动运行相关测试，并按范围执行统一检查。部分科学测试依赖固定 Vina、fixture 原始字节和外部工具；[当前主分支 CI 失败记录](https://github.com/xuxinxi14/DockStart/actions/runs/37024115435) 仍需修复。记录实际失败与跳过项，不能把局部通过写成全部门禁通过。

Run checks appropriate to the change, including the unified gate for code changes. Record failures and skipped external checks with their prerequisites. A passing mock or unit test does not establish scientific accuracy or installer acceptance.

提交 PR 时说明具体问题、修改后的行为、检查命令和结果，以及对已有项目、外部工具或许可证的影响。核心函数要有最小测试；新增依赖同步更新 [许可证记录](docs/license_notes.md)。不要提交第三方源码、工具二进制、真实研究输出或个人设置；经项目明确约定的固定公开 fixture 和许可证资料按已有契约管理。

Use a feature branch and the PR template. Describe the concrete behavior change, validation, project compatibility and any dependency/license impact. Keep external tool invocation in adapters with argument arrays and full logs. Do not change AutoDock Vina's search/scoring algorithm, claim efficacy from docking scores, or bundle optional GPL tools. DockStart contributions remain under [Apache-2.0](LICENSE), with third-party components retaining their own licenses.
