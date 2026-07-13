import type { PageId } from "../navigation/pages";
import type { AppCapabilityProfile, UsageMode } from "../types";
import StatusBadge from "./StatusBadge";

type OnboardingGuideProps = {
  profile?: AppCapabilityProfile | null;
  onNavigate?: (page: PageId) => void;
};

type StartChoice = {
  mode: UsageMode;
  title: string;
  summary: string;
  needs: string;
  readyText: string;
  blockedText: string;
  readyTarget: PageId;
  blockedTarget: PageId;
  readyAction: string;
  blockedAction: string;
};

const choices: StartChoice[] = [
  {
    mode: "basic",
    title: "我已有 PDBQT",
    summary: "直接导入受体和配体 PDBQT，设置 Box 后运行 AutoDock Vina。",
    needs: "需要：AutoDock Vina",
    readyText: "可走最低依赖路径",
    blockedText: "缺少 AutoDock Vina",
    readyTarget: "project-create",
    blockedTarget: "toolchain-status",
    readyAction: "创建项目并导入 PDBQT",
    blockedAction: "先配置 Vina",
  },
  {
    mode: "assisted",
    title: "我只有 PDB / SDF",
    summary: "先获取或导入 raw 文件，再用 Python + RDKit + Meeko 尝试准备 PDBQT。",
    needs: "Assisted 随附：Vina + Python + RDKit + Meeko",
    readyText: "可尝试自动准备",
    blockedText: "缺少自动准备工具链",
    readyTarget: "project-create",
    blockedTarget: "toolchain-status",
    readyAction: "创建项目并获取结构",
    blockedAction: "检查随附工具链",
  },
  {
    mode: "demo",
    title: "我想先看示例",
    summary: "复制小型示例项目，先理解 DockStart 的文件、步骤和结果页面。",
    needs: "需要：示例项目资源",
    readyText: "可打开示例",
    blockedText: "缺少示例资源",
    readyTarget: "project-create",
    blockedTarget: "help",
    readyAction: "打开示例入口",
    blockedAction: "查看说明",
  },
];

function isModeAvailable(profile: AppCapabilityProfile | null | undefined, mode: UsageMode): boolean | null {
  if (!profile) return null;
  if (mode === "basic") return profile.basic_mode_available;
  if (mode === "assisted") return profile.assisted_mode_available;
  if (mode === "demo") return profile.demo_mode_available;
  return false;
}

function modeLabel(mode: string): string {
  if (mode === "basic") return "Basic Mode";
  if (mode === "assisted") return "Assisted Mode";
  if (mode === "demo") return "Demo Mode";
  return "配置";
}

function recommendedTitle(mode: string): string {
  if (mode === "basic") return "建议从已有 PDBQT 开始";
  if (mode === "assisted") return "建议使用自动准备流程";
  if (mode === "demo") return "建议先打开示例项目";
  return "建议先配置工具链";
}

function blockingSummary(profile: AppCapabilityProfile | null | undefined): string[] {
  if (!profile) return ["正在读取工具链和示例项目状态。"];
  if (profile.blocking_items.length === 0) return ["当前没有关键阻塞项。"];
  return profile.blocking_items.slice(0, 5).map((item) => `${modeLabel(item.mode)}：${item.message}`);
}

export default function OnboardingGuide({ profile, onNavigate }: OnboardingGuideProps) {
  const recommendedMode = profile?.recommended_mode ?? "setup";
  return (
    <div className="first-run-guide">
      <header className="first-run-header">
        <div>
          <span className="eyebrow">首次使用</span>
          <h2>你想怎么开始？</h2>
          <p>选择最接近你手头数据的路径。RDKit / Meeko 缺失不会阻止已有 PDBQT 的最低依赖流程。</p>
        </div>
        <StatusBadge tone={profile?.recommended_mode === "setup" ? "warning" : "info"}>
          {recommendedTitle(recommendedMode)}
        </StatusBadge>
      </header>

      <div className="onboarding-choice-grid">
        {choices.map((choice) => {
          const available = isModeAvailable(profile, choice.mode);
          const recommended = recommendedMode === choice.mode;
          const target = available === false ? choice.blockedTarget : choice.readyTarget;
          const action = available === false ? choice.blockedAction : choice.readyAction;
          const statusText = available === null ? "检测中" : available ? choice.readyText : choice.blockedText;
          const tone = available === null ? "muted" : available ? "ok" : "warning";
          return (
            <article className={recommended ? "onboarding-choice recommended" : "onboarding-choice"} key={choice.mode}>
              <div className="onboarding-choice-title">
                <strong>{choice.title}</strong>
                <StatusBadge tone={tone}>{statusText}</StatusBadge>
              </div>
              <p>{choice.summary}</p>
              <span className="onboarding-choice-needs">{choice.needs}</span>
              {onNavigate ? (
                <button className={recommended ? "primary-button" : "secondary-button"} type="button" onClick={() => onNavigate(target)}>
                  {action}
                </button>
              ) : null}
            </article>
          );
        })}
      </div>

      <section className="first-run-next">
        <div>
          <strong>当前缺什么？</strong>
          <ul>
            {blockingSummary(profile).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
        <div>
          <strong>下一步做什么？</strong>
          <p>{profile?.next_action ?? "读取状态后会给出建议。你也可以先打开示例或创建项目。"}</p>
        </div>
      </section>
    </div>
  );
}
