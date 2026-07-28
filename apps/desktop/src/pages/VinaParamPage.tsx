import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import WarningCallout from "../components/WarningCallout";
import type { DockStartProject, ProjectResponse } from "../types";
import {
  VINA_NUMERIC_ADVANCED_KEYS,
  customizedAdvancedVinaCount,
  parseVinaForm,
  resetAdvancedVinaFields,
  vinaSettingsToForm,
  type VinaForm,
  type VinaNumericKey,
  type VinaTextKey,
} from "../utils/vinaForm";

type VinaParamPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
  onOpenVinaConfig: (project: DockStartProject) => void;
};

const vinaFields: Array<{ key: VinaNumericKey; label: string; hint: string; inputMode: "numeric" | "decimal" }> = [
  { key: "exhaustiveness", label: "搜索彻底程度", hint: "建议 8", inputMode: "numeric" },
  { key: "num_modes", label: "输出构象数量", hint: "建议 9", inputMode: "numeric" },
  { key: "energy_range", label: "能量范围", hint: "kcal/mol", inputMode: "decimal" },
  { key: "cpu", label: "CPU 核心数", hint: "0 表示自动", inputMode: "numeric" },
  { key: "seed", label: "随机种子", hint: "可留空", inputMode: "numeric" },
];

type VinaNumericAdvancedKey = (typeof VINA_NUMERIC_ADVANCED_KEYS)[number];

const advancedVinaFields: Array<{
  key: Exclude<VinaNumericAdvancedKey, "verbosity">;
  label: string;
  hint: string;
  inputMode: "numeric" | "decimal";
}> = [
  { key: "max_evals", label: "每次搜索评估上限", hint: "0 = Vina 自动；仅全局对接", inputMode: "numeric" },
  { key: "min_rmsd", label: "构象最小间距", hint: "默认 1 Å；仅全局对接", inputMode: "decimal" },
  { key: "spacing", label: "网格间距", hint: "默认 0.375 Å；AD4 maps 不使用", inputMode: "decimal" },
];

function parseProjectResponse(rawPayload: string): ProjectResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ProjectResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir,
    project: parsed.project ?? null,
    vina: parsed.vina,
    warnings: parsed.warnings ?? [],
    message: parsed.message,
    error: parsed.error,
  };
}

function vinaToForm(project: DockStartProject): VinaForm {
  return vinaSettingsToForm(project.vina);
}

function hasPreparedFiles(project: DockStartProject): boolean {
  return Boolean(project.receptor.file && project.ligand.file);
}

function isValidVinaParams(vina: DockStartProject["vina"]): boolean {
  return parseVinaForm(vinaSettingsToForm(vina)) !== null;
}

export default function VinaParamPage({
  project: initialProject,
  onBack,
  onProjectChange,
  onOpenVinaConfig,
}: VinaParamPageProps) {
  const [project, setProject] = useState<DockStartProject>(initialProject);
  const [vinaForm, setVinaForm] = useState<VinaForm>(() => vinaToForm(initialProject));
  const [message, setMessage] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [canOpenConfig, setCanOpenConfig] = useState(false);

  const applyProjectResponse = useCallback(
    (response: ProjectResponse, fallbackMessage: string) => {
      if (response.ok && response.project) {
        setProject(response.project);
        setVinaForm(vinaToForm(response.project));
        onProjectChange(response.project);
        setMessage(response.message ?? fallbackMessage);
        setWarnings(response.warnings ?? []);
        setRawError("");
        setCanOpenConfig(isValidVinaParams(response.project.vina));
        return;
      }
      setMessage(response.error?.message ?? "Vina 参数保存失败。");
      setWarnings([]);
      setRawError(response.error?.raw_error ?? "");
      setCanOpenConfig(false);
    },
    [onProjectChange],
  );

  const reloadVina = useCallback(async () => {
    setIsBusy(true);
    setCanOpenConfig(false);
    try {
      const rawPayload = await invoke<string>("get_vina_params", {
        projectDir: initialProject.project_dir,
      });
      applyProjectResponse(parseProjectResponse(rawPayload), "Vina 参数已刷新。");
    } catch (error) {
      setMessage("无法读取 Vina 参数。");
      setWarnings([]);
      setRawError(error instanceof Error ? error.message : String(error));
      setCanOpenConfig(false);
    } finally {
      setIsBusy(false);
    }
  }, [applyProjectResponse, initialProject.project_dir]);

  useEffect(() => {
    void reloadVina();
  }, [reloadVina]);

  const updateField = (key: VinaTextKey, value: string) => {
    setCanOpenConfig(false);
    setVinaForm((current) => ({ ...current, [key]: value }));
  };

  const saveVina = async () => {
    const parsedVina = parseVinaForm(vinaForm);
    if (!parsedVina) {
      setCanOpenConfig(false);
      setMessage("Vina 参数格式无效，请检查输入值。");
      setWarnings([]);
      setRawError("");
      return;
    }
    setIsBusy(true);
    setCanOpenConfig(false);
    setMessage("");
    setWarnings([]);
    setRawError("");
    try {
      const rawPayload = await invoke<string>("update_vina_params", {
        projectDir: project.project_dir,
        vinaJson: JSON.stringify(parsedVina),
      });
      applyProjectResponse(parseProjectResponse(rawPayload), "Vina 参数已保存。");
    } catch (error) {
      setMessage("无法保存 Vina 参数。");
      setRawError(error instanceof Error ? error.message : String(error));
      setCanOpenConfig(false);
    } finally {
      setIsBusy(false);
    }
  };

  const advancedCustomCount = customizedAdvancedVinaCount(vinaForm, VINA_NUMERIC_ADVANCED_KEYS);

  return (
    <PageShell labelledBy="vina-param-title">
      <PageHero
        eyebrow="运行对接"
        title="设置 Vina 参数"
        titleId="vina-param-title"
        description="保存本次对接使用的 Vina 参数。"
        actions={
          <>
          <ActionButton variant="text" onClick={onBack}>返回</ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
            <div className="status-strip">
              <article className="metric-card">
                <span>受体 PDBQT</span>
                <strong>{project.receptor.file || "未导入"}</strong>
                <StatusBadge tone={project.receptor.file ? "ok" : "warning"}>{project.receptor.file ? "已完成" : "缺失"}</StatusBadge>
              </article>
              <article className="metric-card">
                <span>配体 PDBQT</span>
                <strong>{project.ligand.file || "未导入"}</strong>
                <StatusBadge tone={project.ligand.file ? "ok" : "warning"}>{project.ligand.file ? "已完成" : "缺失"}</StatusBadge>
              </article>
              <article className="metric-card">
                <span>Box</span>
                <strong>
                  {project.box.center_x}, {project.box.center_y}, {project.box.center_z} / {project.box.size_x}, {project.box.size_y}, {project.box.size_z}
                </strong>
                <StatusBadge tone="ok">已记录</StatusBadge>
              </article>
            </div>

            {!hasPreparedFiles(project) ? (
              <WarningCallout title="输入文件缺失">
                <p>可以先保存参数，但生成配置前需要补全受体和配体 PDBQT。</p>
              </WarningCallout>
            ) : null}

            <SectionCard title="Vina 参数">
              <div className="param-form">
                <label className="param-field">
                  <span>评分函数</span>
                  <select value={vinaForm.scoring} onChange={(event) => updateField("scoring", event.target.value)}>
                    <option value="vina">Vina</option>
                    <option value="vinardo">Vinardo</option>
                    <option value="ad4" disabled>AutoDock4（需要 affinity maps）</option>
                  </select>
                  <small>不同评分函数的分值不能直接比较</small>
                </label>
                {vinaFields.map((field) => (
                  <label className="param-field" key={field.key}>
                    <span>{field.label}</span>
                    <input
                      type="text"
                      value={vinaForm[field.key]}
                      onChange={(event) => updateField(field.key, event.target.value)}
                      inputMode={field.inputMode}
                    />
                    <small>{field.hint}</small>
                  </label>
                ))}
              </div>
              <AdvancedDetails
                summary={`高级设置 · ${advancedCustomCount ? `已自定义 ${advancedCustomCount} 项` : "Vina 默认"}`}
              >
                <div className="param-form">
                  {advancedVinaFields.map((field) => (
                    <label className="param-field" key={field.key}>
                      <span>{field.label}</span>
                      <input
                        type="text"
                        value={vinaForm[field.key]}
                        onChange={(event) => updateField(field.key, event.target.value)}
                        inputMode={field.inputMode}
                      />
                      <small>{field.hint}</small>
                    </label>
                  ))}
                  <label className="param-field">
                    <span>日志详细程度</span>
                    <select value={vinaForm.verbosity} onChange={(event) => updateField("verbosity", event.target.value)}>
                      <option value="1">标准（1）</option>
                      <option value="2">详细（2）</option>
                    </select>
                    <small>详细模式会保存更多 Vina 诊断</small>
                  </label>
                </div>
                <div className="button-row end">
                  <ActionButton
                    variant="text"
                    disabled={isBusy || advancedCustomCount === 0}
                    onClick={() => {
                      setCanOpenConfig(false);
                      setVinaForm((current) => resetAdvancedVinaFields(current, VINA_NUMERIC_ADVANCED_KEYS));
                    }}
                  >
                    恢复 Vina 默认值
                  </ActionButton>
                </div>
              </AdvancedDetails>
              <div className="button-row end">
                <ActionButton variant="text" disabled={isBusy} onClick={() => void reloadVina()}>重新加载</ActionButton>
                <ActionButton variant="primary" disabled={isBusy} onClick={() => void saveVina()}>
                  {isBusy ? "保存中..." : "保存 Vina 参数"}
                </ActionButton>
              </div>
            </SectionCard>

            <div className="next-step-strip">
              <div>
                <strong>{canOpenConfig ? "下一步：生成运行配置" : "保存参数后生成配置"}</strong>
                <p>生成配置只写入 vina_config.txt，不执行对接。</p>
              </div>
              <ActionButton variant="primary" disabled={!canOpenConfig} onClick={() => onOpenVinaConfig(project)}>
                生成运行配置
              </ActionButton>
            </div>

            {warnings.map((warning) => (
              <WarningCallout key={warning} title="参数提示">
                <p>{warning}</p>
              </WarningCallout>
            ))}

            {message ? <p className="message-line">{message}</p> : null}
            {rawError ? (
              <AdvancedDetails>
                <pre>{rawError}</pre>
              </AdvancedDetails>
            ) : null}
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title="输入状态">
            <dl className="mode-context-list">
              <div>
                <dt>受体</dt>
                <dd>{project.receptor.file ? "已导入" : "缺失"}</dd>
              </div>
              <div>
                <dt>配体</dt>
                <dd>{project.ligand.file ? "已导入" : "缺失"}</dd>
              </div>
              <div>
                <dt>Box</dt>
                <dd>已记录</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="当前参数">
            <dl className="mode-context-list">
              <div>
                <dt>评分函数</dt>
                <dd>{project.vina.scoring === "vinardo" ? "Vinardo" : "Vina"}</dd>
              </div>
              <div>
                <dt>搜索彻底程度</dt>
                <dd>{project.vina.exhaustiveness}</dd>
              </div>
              <div>
                <dt>构象数量</dt>
                <dd>{project.vina.num_modes}</dd>
              </div>
              <div>
                <dt>CPU</dt>
                <dd>{project.vina.cpu}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="下一步">
            <p>{canOpenConfig ? "生成 vina_config.txt。" : "保存参数后继续。"}</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
