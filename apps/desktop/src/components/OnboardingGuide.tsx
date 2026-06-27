import type { PageId } from "../navigation/pages";

type OnboardingGuideProps = {
  onNavigate?: (page: PageId) => void;
};

const guideSteps: Array<{ title: string; detail: string; target?: PageId; action?: string }> = [
  {
    title: "1. 创建项目",
    detail: "项目目录会保存原始结构、Vina 输入、运行配置、对接运行、结果和实验记录。",
    target: "project-create",
    action: "创建项目",
  },
  {
    title: "2. 获取结构或导入 PDBQT",
    detail: "原始结构来自 RCSB/PubChem；AutoDock Vina 真正需要准备后的 PDBQT。",
    target: "structure-fetch",
    action: "获取结构",
  },
  {
    title: "3. 准备 Vina 输入",
    detail: "使用已配置 Python + RDKit/Meeko，或手动导入 prepared/receptor.pdbqt 与 prepared/ligand.pdbqt。",
    target: "preparation",
    action: "准备 Vina 输入",
  },
  {
    title: "4. 设置搜索范围与参数",
    detail: "对接箱体只是搜索空间设置，不代表自动识别结合口袋。",
    target: "box-setup",
    action: "设置搜索范围",
  },
  {
    title: "5. 生成配置并运行",
    detail: "生成 configs/vina_config.txt，创建对接运行记录，再执行 Vina。",
    target: "vina-config",
    action: "进入 Vina 运行",
  },
  {
    title: "6. 解析、查看、导出",
    detail: "解析 scores.csv、查看对接构象几何位置、导出 Markdown 实验记录。",
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
