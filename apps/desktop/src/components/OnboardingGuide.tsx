import type { PageId } from "../navigation/pages";

type OnboardingGuideProps = {
  onNavigate?: (page: PageId) => void;
};

const guideSteps: Array<{ title: string; detail: string; target?: PageId; action?: string }> = [
  {
    title: "1. 创建项目",
    detail: "项目目录会保存 raw、prepared、configs、runs、results 和 reports。",
    target: "project-create",
    action: "创建项目",
  },
  {
    title: "2. 获取 raw 或导入 PDBQT",
    detail: "raw 文件来自 RCSB/PubChem；AutoDock Vina 真正需要 prepared PDBQT。",
    target: "structure-fetch",
    action: "获取结构",
  },
  {
    title: "3. 准备 PDBQT",
    detail: "使用已配置 Python + RDKit/Meeko，或手动导入 prepared/receptor.pdbqt 与 prepared/ligand.pdbqt。",
    target: "preparation",
    action: "准备 PDBQT",
  },
  {
    title: "4. 设置 Box 与 Vina 参数",
    detail: "Box 只是搜索空间设置，不代表自动识别结合口袋。",
    target: "box-setup",
    action: "设置 Box",
  },
  {
    title: "5. 生成 config 并运行",
    detail: "生成 configs/vina_config.txt，准备 run metadata，再执行 Vina。",
    target: "vina-config",
    action: "进入 Vina 运行",
  },
  {
    title: "6. 解析、查看、导出",
    detail: "解析 scores.csv、查看 pose 几何位置、导出 Markdown 报告。",
    target: "result",
    action: "查看结果",
  },
];

export default function OnboardingGuide({ onNavigate }: OnboardingGuideProps) {
  return (
    <div className="onboarding-guide">
      {guideSteps.map((step) => (
        <article className="onboarding-step" key={step.title}>
          <strong>{step.title}</strong>
          <p>{step.detail}</p>
          {onNavigate && step.target ? (
            <button className="text-button inline" type="button" onClick={() => onNavigate(step.target!)}>
              {step.action}
            </button>
          ) : null}
        </article>
      ))}
    </div>
  );
}
