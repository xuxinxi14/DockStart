import ActionButton from "../components/ActionButton";
import OnboardingGuide from "../components/OnboardingGuide";
import PageHeader from "../components/PageHeader";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import SectionCard from "../components/SectionCard";
import WarningCallout from "../components/WarningCallout";
import type { PageId } from "../navigation/pages";
import type { DockStartProject } from "../types";

type HelpPageProps = {
  project: DockStartProject | null;
  onNavigate: (page: PageId) => void;
};

export default function HelpPage({ project, onNavigate }: HelpPageProps) {
  return (
    <section className="workbench-page">
      <PageHeader
        eyebrow="帮助"
        title="DockStart 快速帮助"
        description="理解流程、文件类型和工具边界。"
        actions={
          <ActionButton variant="primary" onClick={() => onNavigate(project ? "home" : "project-create")}>
            {project ? "回到总览" : "创建项目"}
          </ActionButton>
        }
      />

      <SectionCard title="首次使用 DockStart">
        <OnboardingGuide onNavigate={onNavigate} />
      </SectionCard>

      <SectionCard title="三种开始方式">
        <div className="help-grid">
          <article>
            <strong>已有 PDBQT</strong>
            <p>这是最低依赖路径。只要 Vina 可用，就能导入受体和配体 PDBQT，继续 Box、配置、运行和结果流程。</p>
          </article>
          <article>
            <strong>只有 PDB / SDF</strong>
            <p>需要配置带 RDKit 和 Meeko 的 Python 环境。缺少这些工具时，只有自动准备会不可用。</p>
          </article>
          <article>
            <strong>先看示例</strong>
            <p>示例项目用于理解软件步骤和页面关系。示例结构不能用于科研结论，也不能证明真实结合。</p>
          </article>
          <article>
            <strong>当前缺什么</strong>
            <p>回到总览或工具链页查看可用模式。Vina 缺失会阻塞 Basic Mode；RDKit/Meeko 缺失只影响 Assisted Mode。</p>
          </article>
        </div>
      </SectionCard>

      <SectionCard title="文件怎么分">
        <div className="help-grid">
          <article>
            <strong>原始结构文件</strong>
            <p>PDB/CIF/SDF/MOL 等 raw 文件，用于后续准备。</p>
          </article>
          <article>
            <strong>Vina 输入文件</strong>
            <p>prepared/receptor.pdbqt 和 prepared/ligand.pdbqt。</p>
          </article>
          <article>
            <strong>运行配置</strong>
            <p>configs/vina_config.txt，来自 PDBQT、Box 和 Vina 参数。</p>
          </article>
          <article>
            <strong>实验记录</strong>
            <p>保存命令、日志、scores 和 Markdown 报告。</p>
          </article>
        </div>
      </SectionCard>

      <SectionCard title="常见卡点">
        <div className="help-grid">
          <article>
            <strong>没有 Vina</strong>
            <p>先到工具链页配置 vina.exe。</p>
          </article>
          <article>
            <strong>RDKit / Meeko 缺失</strong>
            <p>自动准备 PDBQT 需要可用 Python 环境。</p>
          </article>
          <article>
            <strong>raw 不能运行</strong>
            <p>raw 文件需要准备成 PDBQT。</p>
          </article>
          <article>
            <strong>没有结果</strong>
            <p>先完成 Vina 运行，再解析 scores.csv。</p>
          </article>
        </div>
      </SectionCard>

      <WarningCallout title="科学边界">
        <p>DockStart 记录 docking 过程和对接评分，不判断药效或真实结合。</p>
      </WarningCallout>
      <ScientificDisclaimer kind="score" />
    </section>
  );
}
