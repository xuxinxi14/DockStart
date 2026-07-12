# DockStart 高端工业主题迁移报告

日期：2026-07-12  
版本：DockStart v0.9.4  
范围：只调整主题 Token、颜色层级、边框、阴影、状态色与控件表面；未修改页面布局、导航层级、业务逻辑或组件功能。

## 1. 主题结果

中央区域采用低饱和冷灰蓝工业工作台，亮度关系已经固定为：

```text
workspace #D7E0E8
  < panel #EDF1F5
  < raised #F5F7F9
  < input #F8FAFB
```

深色结构区域分别使用：

```text
Sidebar   #041B30
Topbar    #051D34
Statusbar #03182B
RightRail #082846
```

没有使用大面积纯白、纯黑、渐变背景、玻璃拟态、霓虹或发光边框。

## 2. 新增与调整的主题 Token

`apps/desktop/src/styles/tokens.css` 当前集中定义 158 个唯一 Token。主题重构涉及以下语义组：

| Token 组 | 主要 Token |
| --- | --- |
| 应用背景 | `--ds-bg-app`、`--ds-bg-workspace` |
| 三层表面 | `--ds-surface-panel`、`--ds-surface-raised`、`--ds-surface-input` |
| 交互表面 | `--ds-surface-hover`、`--ds-surface-selected` |
| 深色结构 | `--ds-nav-bg`、`--ds-topbar-bg`、`--ds-statusbar-bg`、`--ds-rail-bg`、`--ds-rail-bg-hover` |
| Viewer | `--ds-viewer-bg`、`--ds-viewer-bg-raised`、`--ds-viewer-bg-soft`、`--ds-viewer-border` |
| 边框与分隔 | `--ds-border-strong`、`--ds-border-default`、`--ds-border-subtle`、`--ds-divider`、`--ds-rail-divider` |
| 浅色区文字 | `--ds-text-strong`、`--ds-text-primary`、`--ds-text-secondary`、`--ds-text-muted`、`--ds-text-disabled` |
| 深色区文字 | `--ds-text-on-dark`、`--ds-text-on-dark-secondary`、`--ds-text-on-dark-muted`、`--ds-text-on-dark-accent` |
| 品牌蓝 | `--ds-brand`、`--ds-brand-hover`、`--ds-brand-active`、`--ds-brand-soft`、`--ds-brand-border` |
| 状态 | `--ds-success*`、`--ds-warning*`、`--ds-danger*`、`--ds-info*`、`--ds-neutral*` |
| Focus | `--ds-focus-outline`、`--ds-focus-ring`、`--ds-focus-ring-strong` |
| 阴影 | `--ds-shadow-soft`、`--ds-shadow-panel`、`--ds-shadow-raised`、`--ds-shadow-rail`、`--ds-shadow-viewer` |

旧的 `--ds-bg-panel`、`--ds-navy-*`、`--ds-accent*` 等名称保留为兼容别名，全部指向新的语义 Token，以避免改变现有组件结构。

## 3. 硬编码颜色迁移

审计范围：`apps/desktop/src` 下的 CSS、TS 和 TSX。

| 指标 | 迁移前 | 迁移后 |
| --- | ---: | ---: |
| `tokens.css` 外 Hex / RGB / RGBA / white / black 出现次数 | 130 | 0 |
| `tokens.css` 外唯一硬编码颜色 | 102 | 0 |
| 大面积纯白或纯黑可见元素 | 未系统验证 | 0 |

共迁移 130 处 UI 硬编码颜色。普通工作区渐变已删除；仅开发模式布局网格继续使用 `linear-gradient` 绘制网格，网格颜色已经来自 `--ds-debug-grid-line`，不会出现在生产 UI 的默认状态。

## 4. 有意保留的非 UI 颜色

UI 主题颜色未留下 Token 外残留。以下内容不属于 UI 主题：

- `apps/desktop/src/pages/ViewerPage.tsx:209,408,412,415,427,452,460`：3Dmol 的 `cyan`、`blue`、`spectrum`、`magentaCarbon`、`greenCarbon` 科学渲染协议值，用于分子、Box 和构象着色。
- `apps/desktop/public/dockstart-icon.svg:2-6`：现有 DockStart 品牌图标自身的 SVG 填充与描边，作为独立品牌资产保留。
- CSS 的 `transparent` 与 `currentColor`：表示无填充或继承当前语义颜色，不属于硬编码主题色。

## 5. 修改文件

### 主题与样式

- `apps/desktop/src/styles/tokens.css`
- `apps/desktop/src/styles/instrument-console.css`
- `apps/desktop/src/styles/workbench.css`
- `apps/desktop/src/styles/layout.css`
- `apps/desktop/src/styles/components.css`

### Token API 桥接

- `apps/desktop/src/pages/ViewerPage.tsx`：3Dmol 画布背景改为读取 `--ds-viewer-bg`。
- `apps/desktop/src/components/layout/LayoutDebugOverlay.tsx`：透明背景判断改为读取 `--ds-color-transparent`。

### 设计文档

- `docs/design/dockstart_design_system.md`
- `docs/design/molecular_workbench_theme_tokens.md`
- `docs/design/industrial_theme_migration_report.md`

## 6. 核心页面主题验收

| 页面 | 中央区不刺眼 | Panel 非纯白 | Input 层级 | RightRail/按钮 | 1536×864 | 1920×1080 |
| --- | --- | --- | --- | --- | --- | --- |
| 项目总览 | 通过 | 通过 | 不适用 | 通过 | 通过 | 通过 |
| 创建基础项目 | 通过 | 通过 | 通过 | 通过 | 通过 | 通过 |
| 创建原始结构项目 | 通过 | 通过 | 通过 | 通过 | 通过 | 通过 |
| 示例流程 | 通过 | 通过 | 通过 | 通过 | 通过 | 通过 |
| 获取结构 | 通过 | 通过 | 通过 | 通过 | 通过 | 通过 |
| 准备 Vina 输入 | 通过 | 通过 | 通过 | 通过 | 通过 | 通过 |
| 设置搜索范围 | 通过 | 通过 | 通过 | 通过 | 通过 | 通过 |
| Vina 参数 | 通过 | 通过 | 通过 | 通过 | 通过 | 通过 |
| 运行对接 | 通过 | 通过 | 不适用 | 通过 | 通过 | 通过 |
| 结果 | 通过 | 通过 | 不适用 | 通过 | 通过 | 通过 |
| 报告 | 通过 | 通过 | 不适用 | 通过 | 通过 | 通过 |
| 工具链 | 通过 | 通过 | 不适用 | 通过 | 通过 | 通过 |

额外检查：3D Viewer、工具路径设置和帮助页均在两档分辨率下检查通过。

## 7. 视觉证据

- 1536 × 864：`.codex-ui-audit/implementation/industrial-theme/1536x864/`
- 1920 × 1080：`.codex-ui-audit/implementation/industrial-theme/1920x1080/`

实机 computed style：

| 表面 | RGB | 相对亮度 |
| --- | --- | ---: |
| Workspace | `rgb(215, 224, 232)` | 0.7358 |
| Panel | `rgb(237, 241, 245)` | 0.8751 |
| Raised | `rgb(245, 247, 249)` | 0.9277 |
| Input | `rgb(248, 250, 251)` | 0.9529 |

自动扫描未发现面积超过 50,000 px² 的纯白或纯黑可见元素。

## 8. 构建与测试

| 检查 | 结果 |
| --- | --- |
| `npm run build` | 通过；主入口 357.47 kB，Viewer 独立分包 618.22 kB |
| `python -m unittest discover -s backend/tests` | 通过；260 tests |
| `npm run build:desktop` | 通过；Release EXE、MSI、NSIS 均生成 |
| `git diff --check` | 通过；仅有 Windows CRLF 提示 |
| Token 外硬编码颜色扫描 | 通过；0 处 |

生成产物：

| 产物 | 大小 | SHA256 |
| --- | ---: | --- |
| `apps/desktop/src-tauri/target/release/dockstart-desktop.exe` | 9,546,240 B | `6500007FAFCD2F614B53FAF822FE899A2F5008B8A809769596FAD3031B5D102D` |
| `apps/desktop/src-tauri/target/release/bundle/msi/DockStart_0.9.4_x64_en-US.msi` | 163,349,540 B | `898A009A11164912AE96304971CDB16B6FA56E2347D0000849B355852E9C8AC7` |
| `apps/desktop/src-tauri/target/release/bundle/nsis/DockStart_0.9.4_x64-setup.exe` | 98,947,808 B | `AF464EB1CD1B1C1F981C5ECBAECDE029A2CC59F86F58EC250AB9E2995327993C` |

已知非阻塞警告：3Dmol 上游包使用 `eval`，Viewer 独立分包超过 500 kB；本轮未改变依赖或功能，因此未扩展处理范围。

## 9. 最终结论

主题迁移满足“高端工业科研工作台”目标，中央区域不再使用刺眼的浅蓝加纯白面板组合，深色结构区与冷灰蓝工作区形成稳定层级。未修改布局、导航、业务逻辑或项目数据结构。

final result: passed
