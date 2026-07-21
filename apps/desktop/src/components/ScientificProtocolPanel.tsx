import type { ChangeEvent } from "react";

import "../styles/scientific-protocols.css";

export type ReceptorProtocol = "rigid" | "flexible";
export type MacrocycleMode = "auto" | "rigid";
export type LigandReadiness = "ready" | "warning" | "error";

export type BatchLigandSummaryItem = {
  id: string;
  name: string;
  source?: string;
  status?: LigandReadiness;
  note?: string;
};

export type BatchLigandSummary = {
  total: number;
  ready: number;
  warning: number;
  error: number;
  preview: BatchLigandSummaryItem[];
};

export type ScientificProtocolState = {
  receptorProtocol: ReceptorProtocol;
  flexibleResidues: string[];
  batchScreening: {
    enabled: boolean;
    ligandSummary: BatchLigandSummary;
  };
  resources: {
    cpuPerTask: number;
    retryCount: number;
    topN: number;
  };
  macrocycle: {
    mode: MacrocycleMode;
    minRingSize: number;
    doubleBondPenalty: number;
    allowAromaticBreaks: boolean;
    keepChordedRings: boolean;
    keepEquivalentRings: boolean;
  };
};

export type ScientificProtocolPanelProps = {
  value: ScientificProtocolState;
  onChange: (nextValue: ScientificProtocolState) => void;
  disabled?: boolean;
  className?: string;
  idPrefix?: string;
};

export function createDefaultScientificProtocolState(): ScientificProtocolState {
  return {
    receptorProtocol: "rigid",
    flexibleResidues: [],
    batchScreening: {
      enabled: false,
      ligandSummary: {
        total: 0,
        ready: 0,
        warning: 0,
        error: 0,
        preview: [],
      },
    },
    resources: {
      cpuPerTask: 1,
      retryCount: 0,
      topN: 10,
    },
    macrocycle: {
      mode: "auto",
      minRingSize: 7,
      doubleBondPenalty: 50,
      allowAromaticBreaks: false,
      keepChordedRings: false,
      keepEquivalentRings: false,
    },
  };
}

const readinessLabels: Record<LigandReadiness, string> = {
  ready: "已就绪",
  warning: "需检查",
  error: "不可运行",
};

function finiteNumber(event: ChangeEvent<HTMLInputElement>, fallback: number): number {
  const nextValue = Number(event.currentTarget.value);
  return Number.isInteger(nextValue) ? nextValue : fallback;
}

const flexibleResiduePattern = /^[A-Za-z0-9_.-]{1,8}:-?\d+(?::[A-Za-z0-9])?$/;

export default function ScientificProtocolPanel({
  value,
  onChange,
  disabled = false,
  className = "",
  idPrefix = "scientific-protocol",
}: ScientificProtocolPanelProps) {
  const update = <Key extends keyof ScientificProtocolState,>(
    key: Key,
    nextValue: ScientificProtocolState[Key],
  ) => onChange({ ...value, [key]: nextValue });

  const updateResources = (next: Partial<ScientificProtocolState["resources"]>) =>
    update("resources", { ...value.resources, ...next });

  const updateMacrocycle = (next: Partial<ScientificProtocolState["macrocycle"]>) =>
    update("macrocycle", { ...value.macrocycle, ...next });

  const replaceFlexibleResidue = (index: number, residue: string) => {
    const nextResidues = [...value.flexibleResidues];
    nextResidues[index] = residue;
    update("flexibleResidues", nextResidues);
  };

  const removeFlexibleResidue = (index: number) =>
    update(
      "flexibleResidues",
      value.flexibleResidues.filter((_, residueIndex) => residueIndex !== index),
    );

  const summary = value.batchScreening.ligandSummary;
  const flexibleSelectionMissing =
    value.receptorProtocol === "flexible" && !value.flexibleResidues.some((residue) => residue.trim());
  const invalidFlexibleResidues = value.flexibleResidues.filter(
    (residue) => residue.trim() && !flexibleResiduePattern.test(residue.trim()),
  );
  const batchSelectionMissing = value.batchScreening.enabled && summary.total < 1;

  return (
    <section
      aria-labelledby={`${idPrefix}-title`}
      className={`scientific-protocol-panel ${className}`.trim()}
    >
      <header className="scientific-protocol-header">
        <div>
          <p className="scientific-protocol-eyebrow">科学协议</p>
          <h2 id={`${idPrefix}-title`}>选择对接模式</h2>
        </div>
        <span className="scientific-protocol-baseline">默认：刚性受体 · 单配体</span>
      </header>

      <fieldset className="scientific-protocol-section">
        <legend>受体柔性</legend>
        <p className="scientific-protocol-hint" id={`${idPrefix}-receptor-hint`}>
          柔性模式只放开指定侧链，不会让主链或整个蛋白质自由运动。
        </p>
        <div aria-describedby={`${idPrefix}-receptor-hint`} className="scientific-protocol-choice-grid">
          <label className="scientific-protocol-choice">
            <input
              checked={value.receptorProtocol === "rigid"}
              disabled={disabled}
              name={`${idPrefix}-receptor-mode`}
              onChange={() => update("receptorProtocol", "rigid")}
              type="radio"
            />
            <span>
              <strong>刚性受体</strong>
              <small>兼容当前单次 Vina 流程，速度更快。</small>
            </span>
          </label>
          <label className="scientific-protocol-choice">
            <input
              checked={value.receptorProtocol === "flexible"}
              disabled={disabled}
              name={`${idPrefix}-receptor-mode`}
              onChange={() => update("receptorProtocol", "flexible")}
              type="radio"
            />
            <span>
              <strong>指定柔性侧链</strong>
              <small>需生成匹配的 rigid / flex 受体 PDBQT。</small>
            </span>
          </label>
        </div>

        {value.receptorProtocol === "flexible" ? (
          <div className="scientific-protocol-subsection">
            <div className="scientific-protocol-subsection-heading">
              <div>
                <h3>柔性残基</h3>
                <p id={`${idPrefix}-residue-format`}>每项仅填写链与残基编号，例如 A:315；插入码写作 A:315:B。</p>
              </div>
              <button
                className="scientific-protocol-text-button"
                disabled={disabled}
                onClick={() => update("flexibleResidues", [...value.flexibleResidues, ""])}
                type="button"
              >
                添加残基
              </button>
            </div>
            {value.flexibleResidues.length ? (
              <div className="scientific-protocol-residue-list">
                {value.flexibleResidues.map((residue, index) => (
                  <div className="scientific-protocol-residue-row" key={`${idPrefix}-residue-${index}`}>
                    <label className="scientific-protocol-visually-hidden" htmlFor={`${idPrefix}-residue-${index}`}>
                      柔性残基 {index + 1}
                    </label>
                    <input
                      aria-describedby={`${idPrefix}-residue-format`}
                      disabled={disabled}
                      id={`${idPrefix}-residue-${index}`}
                      onChange={(event) => replaceFlexibleResidue(index, event.currentTarget.value)}
                      placeholder="A:315 或 A:315:B"
                      spellCheck={false}
                      value={residue}
                      aria-invalid={Boolean(residue.trim()) && !flexibleResiduePattern.test(residue.trim())}
                    />
                    <button
                      aria-label={`删除柔性残基 ${index + 1}`}
                      className="scientific-protocol-remove-button"
                      disabled={disabled}
                      onClick={() => removeFlexibleResidue(index)}
                      type="button"
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="scientific-protocol-empty">尚未添加柔性残基。</p>
            )}
            {flexibleSelectionMissing ? (
              <p className="scientific-protocol-warning" role="alert">
                柔性模式至少需要一个残基；运行前还需验证残基编号是否存在于受体中。
              </p>
            ) : null}
            {invalidFlexibleResidues.length ? (
              <p className="scientific-protocol-warning" role="alert">
                柔性残基格式无效：{invalidFlexibleResidues.join("、")}。请使用 A:315 或 A:315:B。
              </p>
            ) : null}
          </div>
        ) : null}
      </fieldset>

      <fieldset className="scientific-protocol-section">
        <legend>配体任务</legend>
        <label className="scientific-protocol-switch">
          <input
            checked={value.batchScreening.enabled}
            disabled={disabled}
            onChange={(event) =>
              update("batchScreening", { ...value.batchScreening, enabled: event.currentTarget.checked })
            }
            type="checkbox"
          />
          <span>
            <strong>批量虚拟筛选</strong>
            <small>多个配体固定串行对接同一受体，不是并行或多配体同时对接。</small>
          </span>
        </label>

        {value.batchScreening.enabled ? (
          <div className="scientific-protocol-subsection">
            <div className="scientific-protocol-summary" aria-label="配体列表摘要">
              <span><strong>{summary.total}</strong> 总数</span>
              <span><strong>{summary.ready}</strong> 已就绪</span>
              <span><strong>{summary.warning}</strong> 需检查</span>
              <span><strong>{summary.error}</strong> 不可运行</span>
            </div>
            {summary.preview.length ? (
              <ul className="scientific-protocol-ligand-list">
                {summary.preview.map((ligand) => {
                  const status = ligand.status ?? "ready";
                  return (
                    <li key={ligand.id}>
                      <div>
                        <strong title={ligand.name}>{ligand.name}</strong>
                        <small>{ligand.source || ligand.note || "来源待记录"}</small>
                      </div>
                      <span className={`scientific-protocol-status is-${status}`}>
                        {readinessLabels[status]}
                      </span>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="scientific-protocol-empty">尚未载入配体列表。</p>
            )}
            {batchSelectionMissing ? (
              <p className="scientific-protocol-warning" role="alert">
                启用批量筛选后至少需要一个已准备的配体。
              </p>
            ) : null}
          </div>
        ) : (
          <p className="scientific-protocol-inline-note">单配体流程将保持现有项目和运行记录格式。</p>
        )}
      </fieldset>

      <fieldset className="scientific-protocol-section">
        <legend>运行资源与结果</legend>
        <div className="scientific-protocol-resource-grid">
          <label>
            <span>每任务 CPU</span>
            <input
              disabled={disabled}
              max={256}
              min={1}
              onChange={(event) => updateResources({ cpuPerTask: finiteNumber(event, value.resources.cpuPerTask) })}
              type="number"
              value={value.resources.cpuPerTask}
            />
          </label>
          <label>
            <span>失败重试</span>
            <input
              disabled={disabled || !value.batchScreening.enabled}
              max={10}
              min={0}
              onChange={(event) => updateResources({ retryCount: finiteNumber(event, value.resources.retryCount) })}
              type="number"
              value={value.batchScreening.enabled ? value.resources.retryCount : 0}
            />
          </label>
          <label>
            <span>汇总 Top N</span>
            <input
              disabled={disabled || !value.batchScreening.enabled}
              max={10000}
              min={1}
              onChange={(event) => updateResources({ topN: finiteNumber(event, value.resources.topN) })}
              type="number"
              value={value.resources.topN}
            />
          </label>
        </div>
        <p className="scientific-protocol-inline-note">
          批量任务固定串行执行，每次只运行一个配体；每个任务最多使用 {Math.max(1, value.resources.cpuPerTask)} 个 CPU 线程。
        </p>
      </fieldset>

      <fieldset className="scientific-protocol-section">
        <legend>大环配体准备</legend>
        <p className="scientific-protocol-hint" id={`${idPrefix}-macrocycle-hint`}>
          该设置作用于 Meeko 配体准备，不是 Vina 搜索参数；应在准备快照中记录断环位置。
        </p>
        <div aria-describedby={`${idPrefix}-macrocycle-hint`} className="scientific-protocol-choice-grid">
          <label className="scientific-protocol-choice">
            <input
              checked={value.macrocycle.mode === "auto"}
              disabled={disabled}
              name={`${idPrefix}-macrocycle-mode`}
              onChange={() => updateMacrocycle({ mode: "auto" })}
              type="radio"
            />
            <span><strong>自动断环</strong><small>使用已审计的 Meeko 大环参数。</small></span>
          </label>
          <label className="scientific-protocol-choice">
            <input
              checked={value.macrocycle.mode === "rigid"}
              disabled={disabled}
              name={`${idPrefix}-macrocycle-mode`}
              onChange={() => updateMacrocycle({ mode: "rigid" })}
              type="radio"
            />
            <span><strong>保持刚性</strong><small>不搜索大环环构象，结果依赖输入构象。</small></span>
          </label>
        </div>

        {value.macrocycle.mode === "auto" ? (
          <div className="scientific-protocol-subsection">
            <div className="scientific-protocol-resource-grid is-macrocycle">
              <label>
                <span>最小环尺寸</span>
                <input
                  disabled={disabled}
                  max={33}
                  min={3}
                  onChange={(event) =>
                    updateMacrocycle({ minRingSize: finiteNumber(event, value.macrocycle.minRingSize) })
                  }
                  step={1}
                  type="number"
                  value={value.macrocycle.minRingSize}
                />
              </label>
              <label>
                <span>双键断裂惩罚</span>
                <input
                  disabled={disabled}
                  max={1000}
                  min={0}
                  onChange={(event) =>
                    updateMacrocycle({
                      doubleBondPenalty: finiteNumber(event, value.macrocycle.doubleBondPenalty),
                    })
                  }
                  step={1}
                  type="number"
                  value={value.macrocycle.doubleBondPenalty}
                />
              </label>
            </div>
            <div className="scientific-protocol-option-list">
              <label className="scientific-protocol-switch is-compact">
                <input
                  checked={value.macrocycle.allowAromaticBreaks}
                  disabled={disabled}
                  onChange={(event) => updateMacrocycle({ allowAromaticBreaks: event.currentTarget.checked })}
                  type="checkbox"
                />
                <span>
                  <strong>允许芳香型 A 原子断环</strong>
                  <small>对应 macrocycle_allow_A；仅在有明确结构依据时启用。</small>
                </span>
              </label>
              <label className="scientific-protocol-switch is-compact">
                <input
                  checked={value.macrocycle.keepChordedRings}
                  disabled={disabled}
                  onChange={(event) => updateMacrocycle({ keepChordedRings: event.currentTarget.checked })}
                  type="checkbox"
                />
                <span>
                  <strong>保留带弦环</strong>
                  <small>对应 Meeko keep_chorded_rings。</small>
                </span>
              </label>
              <label className="scientific-protocol-switch is-compact">
                <input
                  checked={value.macrocycle.keepEquivalentRings}
                  disabled={disabled}
                  onChange={(event) => updateMacrocycle({ keepEquivalentRings: event.currentTarget.checked })}
                  type="checkbox"
                />
                <span>
                  <strong>保留等价环</strong>
                  <small>对应 Meeko keep_equivalent_rings。</small>
                </span>
              </label>
            </div>
          </div>
        ) : (
          <p className="scientific-protocol-inline-note">刚性模式不会向 Meeko 传入断环参数。</p>
        )}
      </fieldset>

      <p className="scientific-protocol-footnote">
        所有高级协议都需要在运行前检查输入、工具版本与生成文件；对接评分不能替代实验验证。
      </p>
    </section>
  );
}
