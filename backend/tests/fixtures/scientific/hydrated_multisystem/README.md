# 2BYS / 2ZJU 水合多系统验收元数据

本目录是 **metadata-only** fixture。仓库只保存来源身份和验收契约，不保存
RCSB 结构、准备后的 PDBQT、AutoGrid maps、Vina 输出、报告或可执行文件。

`scripts/verify_hydrated_multisystem.py` 在调用时：

1. 从 `source_manifest.json` 中的 HTTPS 地址下载 2BYS 与 2ZJU 的 PDB/SDF；
2. 对受体 PDB 校验原始下载字节数与 SHA256；对 ModelServer 配体 SDF 校验
   `model_server_sdf_mol_block_lf_v1` 科学内容身份；
3. 使用调用者明确提供的 Python、AutoGrid 和 Vina，按 DockStart 当前公开项目
   API 依次执行原始文件导入、受体/配体准备、水合配体、54³/0.375 Å maps、
   请求最多九构象的 Vina、保留水后处理与报告导出；
4. 校验 Python 3.11.15、RDKit 2026.03.3、Meeko 0.7.1、
   AutoGrid 4.2.7、Vina 1.2.7，以及实际调用路径和 SHA256 的一致性；
5. 无论成功或失败都恢复设置环境变量并删除整个临时目录。

下载文件在导入紧邻前后都必须继续匹配下载时审计的 wire 路径、字节数与
SHA256。原始文件导入后，准备记录中的 `claimed_input`、输入快照、认领校验与
最终输入校验也必须继续匹配本次导入的身份；只校验“下载或导入瞬间”而未绑定
到实际准备输入不能通过验收。

ModelServer SDF 的请求/作业/统计 property 会随等价下载变化，因此不作为
跨次身份 oracle。canonicalization 要求单一完整 SDF record、唯一 `M  END`
且以 `$$$$` 结束；统一为 LF 后，仅保留从文件开头到 `M  END` 的完整 mol
block，并以一个空行结束，作为 canonical 身份；原始 record 的 `$$$$` 只用于
完整性校验，不进入 mol-block 哈希。这样 property 值、长度、顺序或大小写变化
不会误报，而 atom、bond、坐标、charge 等 mol-block 内容的任何变化都会失败。
每次下载的实际 wire 字节数、SHA256 与最终 HTTPS URL 仍写入验收结果用于审计，
但不作为跨次硬门禁。

两套 PDB 都含替代构象。清单通过 `meeko_receptor_controls` 保存逐残基
`wanted_altloc` 决定，固定 `allow_bad_res=false`，且不使用
`default_altloc`、自由命令行或静默删除。这些选择只用于建立可审计的工程链，
不表示 altloc A 是科学最优构象。2BYS 固定 69 个带 altloc 标签的残基
（其中 54 个有多种 altloc，15 个仅标 A），2ZJU 固定 4 个残基。共晶配体以
带组分身份和理由的
`deleted_residues` 逐项冻结：2BYS 删除 A–J 链的 LOB 301，2ZJU 删除
A/C/D/E 链的 IM4 301 及 D:302；命令必须精确生成对应
`--delete_residues`。HOH 沿用 Meeko 既有默认处理并由准备记录留痕，不在这里
逐水声明。

2ZJU 对 `Cl`、`receptor.Cl.map` 和 7 个水设硬门禁；2BYS 对 6 个水设硬门禁。
两套体系均请求 `num_modes=9`；Vina 可以合法返回少于请求数的构象，因此验收
接受 1–9 个从 Mode 1 开始的连续构象，并按实际数量校验原始水、保留水、
强/弱水和置换水守恒关系，不补造缺失 Mode。

清单中的历史最佳评分、W map 哈希和 RMSD 仅是诊断参考。此前探索性探针使用
全局 `default_altloc A`；当前公开 API 使用逐残基、带 canonical SHA256 的
结构化合同，因此历史字节身份不能作为新验收硬门禁。尤其是 2BYS 的
12.760 Å 和 2ZJU 的 6.422 Å RMSD 都属于已知姿势质量负面诊断，不能被解释为
科学重对接成功。

运行示例：

```powershell
python scripts/verify_hydrated_multisystem.py `
  --python C:\path\to\python.exe `
  --autogrid C:\path\to\autogrid4.exe `
  --vina C:\path\to\vina.exe
```

该命令会联网并运行真实外部工具；单元测试只使用合成数据和 mock，不执行真实
下载或对接。CLI 会将标准输出和标准错误显式配置为 UTF-8，以便 Windows
重定向后的 JSON 保持中文可读。
