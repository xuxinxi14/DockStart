import ActionButton from "../components/ActionButton";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import OnboardingGuide from "../components/OnboardingGuide";
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
    <PageShell labelledBy="help-title">
      <PageHero
        eyebrow="帮助"
        title="DockStart 快速帮助"
        titleId="help-title"
        description="理解流程、文件类型和工具边界。"
        actions={
          <ActionButton variant="primary" onClick={() => onNavigate(project ? "home" : "project-create")}>
            {project ? "回到总览" : "创建项目"}
          </ActionButton>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
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
                  <p>Assisted Stable 已随附 RDKit/Meeko，可直接尝试自动准备；兼容的用户配置 Python 仍会优先使用。</p>
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
                  <p>先到工具链页重新检测随附 Vina；如果随附资源不可用，再配置外部 vina.exe。</p>
                </article>
                <article>
                  <strong>RDKit / Meeko 缺失</strong>
                  <p>先重新检测 Assisted 随附工具链。Basic profile 或自定义工作流才需要另行配置兼容 Python。</p>
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

            <SectionCard title="工具链怎么修">
              <div className="help-grid">
                <article>
                  <strong>Vina 缺失</strong>
                  <p>Basic Mode 和真实 docking 都需要 AutoDock Vina。先重新检测随附 Vina；仍不可用时，再到设置页填写外部 vina.exe。</p>
                </article>
                <article>
                  <strong>自动准备缺依赖</strong>
                  <p>先重检随附工具链；如果使用 Basic profile 或需要替换版本，再配置独立 conda 环境。</p>
                </article>
                <article>
                  <strong>Microsoft Store Python</strong>
                  <p>不建议作为自定义 RDKit/Meeko 工具链。可继续使用 Assisted 随附 runtime，或配置独立 conda 环境。</p>
                </article>
                <article>
                  <strong>不会联网改环境</strong>
                  <p>Assisted 的固定工具链随安装包提供；应用运行时不会下载包、修改系统 Python 或自动执行修复命令。</p>
                </article>
              </div>
            </SectionCard>

            <WarningCallout title="科学边界">
              <p>DockStart 记录 docking 过程和对接评分，不判断药效或真实结合。</p>
            </WarningCallout>
            <ScientificDisclaimer kind="score" />
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title="当前入口">
            <dl className="mode-context-list">
              <div>
                <dt>项目状态</dt>
                <dd>{project ? `已加载：${project.project_name}` : "尚未加载项目"}</dd>
              </div>
              <div>
                <dt>建议动作</dt>
                <dd>{project ? "回到总览继续流程" : "先创建或打开项目"}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="最低依赖">
            <p>已有受体和配体 PDBQT 时，只需可用的 AutoDock Vina 即可进入 Basic Mode。</p>
          </RightRailSection>

          <RightRailSection title="排查顺序">
            <p>先确认工具链，再检查 raw 与 prepared 文件是否混用，最后查看运行日志和技术详情。</p>
          </RightRailSection>

          <RightRailSection title="结果边界">
            <p>对接评分只能用于当前结构与参数下的趋势参考，不能证明真实结合或药效。</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
