type DisclaimerKind = "score" | "preparation" | "viewer" | "general";

type ScientificDisclaimerProps = {
  kind?: DisclaimerKind;
};

const text: Record<DisclaimerKind, string> = {
  score: "Docking score 仅供结构结合趋势参考，不能证明真实结合或药效，不能替代实验验证。",
  preparation: "自动准备 PDBQT 仍需要人工检查质子化、电荷、构象、缺失残基、水、金属、辅因子和 Box 合理性。",
  viewer: "3D Viewer 只做几何查看，不做 PLIP/ProLIF、相互作用分析、pocket prediction 或科学验证。",
  general: "DockStart 记录和展示计算流程，但不会自动判断药效、安全性或临床价值。",
};

export default function ScientificDisclaimer({ kind = "general" }: ScientificDisclaimerProps) {
  return <p className="scientific-disclaimer">{text[kind]}</p>;
}
