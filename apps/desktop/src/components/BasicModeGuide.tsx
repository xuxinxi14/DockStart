import ActionButton from "./ActionButton";
import StatusBadge from "./StatusBadge";

type BasicModeGuideProps = {
  onPrimaryAction?: () => void;
  primaryLabel?: string;
  compact?: boolean;
};

export default function BasicModeGuide({
  onPrimaryAction,
  primaryLabel = "开始 Basic Mode",
  compact = false,
}: BasicModeGuideProps) {
  return (
    <article className={compact ? "basic-mode-guide compact" : "basic-mode-guide"}>
      <div className="basic-mode-guide-header">
        <div>
          <span className="eyebrow">最低依赖路径</span>
          <h2>已有 PDBQT → 设置 Box → 运行 Vina → 查看结果</h2>
        </div>
        <StatusBadge tone="ok">只需要 Vina</StatusBadge>
      </div>
      <p>
        如果你已经有受体和配体的 PDBQT 文件，可以跳过 raw 下载和 RDKit/Meeko 自动准备。
        RDKit/Meeko 缺失不会阻止这条路径。
      </p>
      {!compact ? (
        <ol className="inline-flow-list">
          <li>导入 receptor.pdbqt</li>
          <li>导入 ligand.pdbqt</li>
          <li>设置搜索范围</li>
          <li>生成配置并运行 Vina</li>
        </ol>
      ) : null}
      {onPrimaryAction ? (
        <ActionButton variant="primary" onClick={onPrimaryAction}>
          {primaryLabel}
        </ActionButton>
      ) : null}
    </article>
  );
}
