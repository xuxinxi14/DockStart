# v1.0.4 进阶工作流与适用边界

第一次使用请先完成 [基础快速开始](quick_start_v1_0_4.md)。本页概括源码和候选包中已有的进阶入口；不声明所有协议均已通过 v1.0.4 完整安装态验收。实际能否运行仍由工具能力、项目输入与运行前检查决定。

| 工作流 | 适用条件与结果解释 | 在线专题 |
| --- | --- | --- |
| 姿势评分 `score_only` | 评价已有坐标，不搜索新构象；必须在同一受体坐标系中人工复核并确认输入 | [任务类型](https://xuxinxi14.github.io/DockStart-Docs/docs/part-c/faq-task-types/) |
| 局部优化 `local_only` | 先记录输入评分，再局部优化并比较；不等同全局搜索 | [任务类型](https://xuxinxi14.github.io/DockStart-Docs/docs/part-c/faq-task-types/) |
| 有限柔性受体 | 依据结构选择少量侧链，冻结刚性与柔性输入；不让整个蛋白任意运动 | [1FPU](https://xuxinxi14.github.io/DockStart-Docs/docs/part-b/cases/flexible-docking-1fpu/) |
| 串行批量 | 多个配体逐个运行；库可从多文件、目录或多记录 SDF 预览导入；历史归档只读 | [批量](https://xuxinxi14.github.io/DockStart-Docs/docs/part-b/cases/batch-docking/) |
| 恰好两个配体共同对接 | 刚性受体、受支持协议；一个 Mode 是一组联合构象，仅有联合评分，不能拆分成员 affinity | [5X72](https://xuxinxi14.github.io/DockStart-Docs/docs/part-b/cases/multiple-ligands-docking-5x72/) |
| 大环 | 完成分析、确认、准备、继续步骤；检查伪原子与断环策略，准备成功不等于协议验证通过 | [BACE1](https://xuxinxi14.github.io/DockStart-Docs/docs/part-b/cases/macrocycle-docking-bace1/) |
| 标准 AutoDock4 maps | 需要外部 AutoGrid4 或受支持的完整 maps；有独立输入、评分与证据条件 | [AD4 maps](https://xuxinxi14.github.io/DockStart-Docs/docs/part-b/cases/autodock4-maps-workflow/) |
| Vina/Vinardo maps 复用 | 刚性、单配体、全局对接；冻结 maps，为 grid-only/等价 no-refine，不是 AD4 评分 | [Box/maps](https://xuxinxi14.github.io/DockStart-Docs/docs/part-c/faq-box-and-maps/) |
| 水合 AD4 Experimental | 专用单配体准备、网格和后处理；不与普通、柔性或共同对接入口混用 | [1UW6](https://xuxinxi14.github.io/DockStart-Docs/docs/part-b/cases/hydrated-docking-1uw6/) |
| AD4Zn Beta | 受支持的单核 Zn、TZ 与参数条件；外部 AutoGrid4 4.2.7+ 和用户提供 AD4Zn.dat，不能泛化到其他金属 | [AD4Zn](https://xuxinxi14.github.io/DockStart-Docs/docs/part-b/cases/zinc-ad4zn/) |

标准 AD4 的有限柔性单配体、刚性串行批量和刚性双配体路径与 AD4Zn、水合子协议隔离。有关三条 AD4 外部验收器、历史证据和源码绑定要求见 [发布检查表](release/release_checklist.md)。不能把旧版外部证据改写成当前安装包的完整验收。

评分/局部优化的输入确认绑定实际 PDBQT 身份；重新准备或替换输入后需重新确认。参数如 `max_evals`、`min_rmsd`、`spacing`、`verbosity`、`no_refine`、`force_even_voxels` 受适用协议与 Vina 能力门禁限制；`unbound_energy` 只用于受支持的刚性单配体 score_only，不进入全局对接或批量队列。

两份批量归档比较先核对输入身份与协议；仅在条件可比时计算差值。归档 ZIP 是只读实验包，不含完整可恢复活动项目，可能包含本机路径，导出前需检查分享范围。

Docking score 仅供结构结合趋势参考，不能替代实验验证。
