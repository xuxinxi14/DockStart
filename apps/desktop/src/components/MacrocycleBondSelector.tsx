import { lazy, Suspense } from "react";
import { CheckCircle, Info, Warning } from "@phosphor-icons/react";
import type {
  MacrocyclePreparationEvidence,
  MacrocycleReviewOptions,
  MacrocycleSelection,
  MacrocycleStatusResponse,
} from "../types";
import {
  formatZeroBasedBondPairs,
  macrocycleReviewMatchesOptions,
} from "../utils/macrocyclePreparation";
import ActionButton from "./ActionButton";
import StatusBadge from "./StatusBadge";

const StructureMiniPreview = lazy(() => import("./StructureMiniPreview"));

type MacrocycleMode = "standard" | "reviewed";

type MacrocycleBondSelectorProps = {
  projectDir: string;
  mode: MacrocycleMode;
  status: MacrocycleStatusResponse | null;
  options: MacrocycleReviewOptions;
  selection: MacrocycleSelection;
  evidence: MacrocyclePreparationEvidence | null;
  rawReady: boolean;
  busy: boolean;
  prepared: boolean;
  canPrepare: boolean;
  canContinue: boolean;
  onModeChange: (mode: MacrocycleMode) => void;
  onOptionsChange: (options: MacrocycleReviewOptions) => void;
  onReview: () => void;
  onSelectCandidate: (candidateId: string) => void;
  onRestoreDefault: () => void;
  onConfirmCandidate: () => void;
  onConfirmRigid: () => void;
  onResetConfirmation: () => void;
  onPrepare: () => void;
  onContinue: () => void;
};

function statePresentation(status: MacrocycleStatusResponse | null): {
  label: string;
  tone: "ok" | "warning" | "error" | "muted" | "info";
} {
  if (!status) return { label: "未检测", tone: "muted" };
  if (!status.ok || status.state === "stale") return { label: "需重新检测", tone: "error" };
  if (status.state === "confirmed") return { label: "已确认", tone: "ok" };
  if (status.state === "reviewed") return { label: "待确认", tone: "warning" };
  if (status.state === "not_macrocycle") return { label: "未检测到大环", tone: "info" };
  if (status.state === "unsupported") return { label: "柔性断环不可用", tone: "warning" };
  return { label: "未检测", tone: "muted" };
}

function bondLabel(candidateIndex: number, bondIndex: number, bond: {
  atom_numbers_one_based: number[];
  atom_labels: string[];
  bond_type: string;
}): string {
  const [left, right] = bond.atom_numbers_one_based;
  const [leftLabel, rightLabel] = bond.atom_labels;
  return `键 ${candidateIndex + 1}.${bondIndex + 1}：#${left} ${leftLabel || ""} — #${right} ${rightLabel || ""}（${bond.bond_type || "未知键型"}）`;
}

export default function MacrocycleBondSelector({
  projectDir,
  mode,
  status,
  options,
  selection,
  evidence,
  rawReady,
  busy,
  prepared,
  canPrepare,
  canContinue,
  onModeChange,
  onOptionsChange,
  onReview,
  onSelectCandidate,
  onRestoreDefault,
  onConfirmCandidate,
  onConfirmRigid,
  onResetConfirmation,
  onPrepare,
  onContinue,
}: MacrocycleBondSelectorProps) {
  const review = status?.review?.valid ? status.review : null;
  const confirmation = status?.confirmation?.valid ? status.confirmation : null;
  const selectedCandidateId = selection?.kind === "candidate" ? selection.candidateId : "";
  const defaultCandidateId = review?.recommended_candidate_id || "";
  const reviewCurrent = macrocycleReviewMatchesOptions(status, options);
  const selectedCandidateExists = Boolean(
    review?.candidate_sets.some((candidate) => candidate.candidate_id === selectedCandidateId),
  );
  const selectedCandidate = review?.candidate_sets.find(
    (candidate) => candidate.candidate_id === selectedCandidateId,
  ) ?? null;
  const reviewRefreshKey = review
    ? Number.parseInt(review.analysis_sha256.slice(0, 8), 16) || 0
    : 0;
  const state = statePresentation(status);
  const reviewDone = Boolean(review && reviewCurrent);
  const confirmationDone = Boolean(confirmation);

  return (
    <section className="macrocycle-selector" aria-labelledby="macrocycle-selector-title">
      <header className="macrocycle-selector-header">
        <div>
          <span className="macrocycle-selector-eyebrow">MEEKO MACROCYCLE</span>
          <h3 id="macrocycle-selector-title">大环配体准备</h3>
          <p>检测到大环时，依次完成分析、断环选择、确认和转换。</p>
        </div>
        <StatusBadge tone={state.tone}>{state.label}</StatusBadge>
      </header>

      <div className="macrocycle-mode-field">
        <label>
          <span>准备策略</span>
          <select
            disabled={busy}
            value={mode}
            onChange={(event) => onModeChange(event.target.value as MacrocycleMode)}
          >
            <option value="standard">标准准备</option>
            <option value="reviewed">受审查的大环准备</option>
          </select>
        </label>
        {mode === "standard" ? (
          <p>用于普通配体；若检测到大环或 Meeko 拟自动断环，后端会停止并要求审查。</p>
        ) : (
          <p>确认后按当前方案准备；更换配体或修改参数后需重新确认。</p>
        )}
      </div>

      {mode === "reviewed" ? (
        <>
          <ol className="macrocycle-progress" aria-label="大环准备进度">
            <li className={reviewDone ? "is-complete" : "is-current"}><span>1</span><strong>分析候选</strong></li>
            <li className={selectedCandidateExists ? "is-complete" : reviewDone ? "is-current" : ""}><span>2</span><strong>选择方案</strong></li>
            <li className={confirmationDone ? "is-complete" : selectedCandidateExists ? "is-current" : ""}><span>3</span><strong>确认断环</strong></li>
            <li className={prepared ? "is-complete" : confirmationDone ? "is-current" : ""}><span>4</span><strong>转换 PDBQT</strong></li>
          </ol>

          <div className="macrocycle-review-settings">
            <div className="macrocycle-number-fields">
              <label>
                <span>最小环尺寸</span>
                <input
                  type="number"
                  min={7}
                  max={33}
                  disabled={busy}
                  value={options.min_ring_size}
                  onChange={(event) => onOptionsChange({
                    ...options,
                    min_ring_size: Math.max(7, Math.min(33, Number(event.target.value) || 7)),
                  })}
                />
              </label>
              <label>
                <span>最大断环数</span>
                <input
                  type="number"
                  min={1}
                  max={4}
                  disabled={busy}
                  value={options.max_breaks}
                  onChange={(event) => onOptionsChange({
                    ...options,
                    max_breaks: Math.max(1, Math.min(4, Number(event.target.value) || 4)),
                  })}
                />
              </label>
            </div>
            <div className="macrocycle-option-toggles">
              <label className="checkbox-row compact">
                <input
                  type="checkbox"
                  checked={options.allow_atom_type_a_endpoints}
                  disabled={busy}
                  onChange={(event) => onOptionsChange({
                    ...options,
                    allow_atom_type_a_endpoints: event.target.checked,
                  })}
                />
                允许芳香原子断环
              </label>
              <label className="checkbox-row compact">
                <input
                  type="checkbox"
                  checked={options.keep_chorded_rings}
                  disabled={busy}
                  onChange={(event) => onOptionsChange({
                    ...options,
                    keep_chorded_rings: event.target.checked,
                    keep_equivalent_rings: event.target.checked
                      ? true
                      : options.keep_equivalent_rings,
                  })}
                />
                保留弦环候选
              </label>
              <label className="checkbox-row compact">
                <input
                  type="checkbox"
                  checked={options.keep_equivalent_rings}
                  disabled={busy || options.keep_chorded_rings}
                  onChange={(event) => onOptionsChange({
                    ...options,
                    keep_equivalent_rings: event.target.checked,
                  })}
                />
                保留等价候选
              </label>
            </div>
            <ActionButton
              className="macrocycle-review-button"
              variant="primary"
              disabled={busy || !rawReady}
              onClick={onReview}
            >
              {review ? "重新分析候选" : "分析大环候选"}
            </ActionButton>
          </div>

          {!rawReady ? (
            <div className="macrocycle-inline-message">
              <Info aria-hidden="true" size={17} />
              <span>请先导入单分子 SDF、MOL 或 MOL2。</span>
            </div>
          ) : null}

          {review && !reviewCurrent ? (
            <div className="macrocycle-inline-message is-warning">
              <Warning aria-hidden="true" size={17} weight="fill" />
              <span>分析参数已变化，请重新分析后确认。</span>
            </div>
          ) : null}

          {status?.integrity?.issues?.length ? (
            <div className="macrocycle-issues" role="alert">
              {status.integrity.issues.map((issue) => (
                <div key={`${issue.code}-${issue.message}`}>
                  <strong>{issue.title || issue.code}</strong>
                  <span>{issue.message}</span>
                </div>
              ))}
            </div>
          ) : null}

          {review?.is_macrocycle === false ? (
            <div className="macrocycle-inline-message">
              <Info aria-hidden="true" size={17} />
              <span>当前阈值下未检测到大环；可改用标准准备。</span>
            </div>
          ) : null}

          {review?.unsupported_reasons?.length ? (
            <div className="macrocycle-issues">
              {review.unsupported_reasons.map((issue) => (
                <div key={`${issue.code}-${issue.message}`}>
                  <strong>{issue.title || issue.code}</strong>
                  <span>{issue.message}</span>
                </div>
              ))}
            </div>
          ) : null}

          {review?.candidate_sets?.length ? (
            <div className="macrocycle-candidates">
              <div className="macrocycle-candidates-heading">
                <div>
                  <strong>断环组合</strong>
                  <span>{review.candidate_sets.length} 个可审查组合</span>
                </div>
                <ActionButton
                  variant="text"
                  disabled={busy || !defaultCandidateId}
                  onClick={onRestoreDefault}
                >
                  恢复 Meeko 默认
                </ActionButton>
              </div>
              <div className="macrocycle-candidate-workspace">
                <div className="macrocycle-candidate-list" role="radiogroup" aria-label="大环断环组合">
                  {review.candidate_sets.map((candidate, candidateIndex) => {
                    const selected = selectedCandidateId === candidate.candidate_id;
                    const isDefault = candidate.candidate_id === defaultCandidateId;
                    return (
                      <label
                        className={`macrocycle-candidate ${selected ? "is-selected" : ""}`}
                        key={candidate.candidate_id}
                      >
                        <input
                          type="radio"
                          name="macrocycle-candidate"
                          checked={selected}
                          disabled={busy || !reviewCurrent}
                          onChange={() => onSelectCandidate(candidate.candidate_id)}
                        />
                        <span className="macrocycle-candidate-body">
                          <span className="macrocycle-candidate-title">
                            <strong>组合 {candidateIndex + 1}</strong>
                            {isDefault ? <StatusBadge tone="info">Meeko 默认</StatusBadge> : null}
                            {candidate.ring_coverage_complete ? <span>覆盖全部目标环</span> : null}
                          </span>
                          <span className="macrocycle-bond-list">
                            {candidate.bonds.map((bond, bondIndex) => (
                              <code key={`${candidate.candidate_id}-${bondIndex}`}>
                                {bondLabel(candidateIndex, bondIndex, bond)}
                              </code>
                            ))}
                          </span>
                        </span>
                      </label>
                    );
                  })}
                </div>
                <div className="macrocycle-candidate-preview">
                  <strong>当前组合的 3D 位置</strong>
                  <span>橙色原子和连线是已选候选的断环键。</span>
                  <Suspense fallback={<div className="structure-mini-preview structure-mini-preview-loading">正在加载 3D 预览…</div>}>
                    <StructureMiniPreview
                      projectDir={projectDir}
                      fileKind="ligand_raw"
                      label="大环断环候选"
                      refreshKey={reviewRefreshKey}
                      highlightBonds={selectedCandidate?.bonds}
                    />
                  </Suspense>
                </div>
              </div>
            </div>
          ) : null}

          {review?.is_macrocycle ? (
            <div className="macrocycle-confirmation-bar">
              {confirmation ? (
                <>
                  <div>
                    <CheckCircle aria-hidden="true" size={18} weight="fill" />
                    <span>
                      {confirmation.selection_mode === "rigid"
                        ? "已确认刚性大环"
                        : "已确认所选断环组合"}
                    </span>
                  </div>
                  <ActionButton disabled={busy} onClick={onResetConfirmation}>撤销确认</ActionButton>
                </>
              ) : (
                <>
                  <div>
                    <Info aria-hidden="true" size={18} />
                    <span>确认后才能启动正式大环准备。</span>
                  </div>
                  <div className="button-row">
                    <ActionButton
                      variant="primary"
                      disabled={
                        busy
                        || !reviewCurrent
                        || !status?.can_confirm_candidate
                        || !selectedCandidateExists
                      }
                      onClick={onConfirmCandidate}
                    >
                      确认所选断环
                    </ActionButton>
                    {status?.can_confirm_rigid ? (
                      <ActionButton
                        disabled={busy || !reviewCurrent}
                        onClick={onConfirmRigid}
                      >
                        改用刚性大环
                      </ActionButton>
                    ) : null}
                  </div>
                </>
              )}
            </div>
          ) : null}

          {confirmation ? (
            <div className="macrocycle-next-action">
              <div>
                <strong>{prepared ? "配体 PDBQT 已完成" : "断环方案已确认"}</strong>
                <span>{prepared ? (canContinue ? "受体与配体均已就绪，可以设置搜索范围。" : "请完成受体 PDBQT 后继续。") : "按已确认方案生成配体 PDBQT。"}</span>
              </div>
              {prepared ? (
                <ActionButton variant="primary" disabled={busy || !canContinue} onClick={onContinue}>
                  设置搜索范围并继续
                </ActionButton>
              ) : (
                <ActionButton variant="primary" disabled={busy || !canPrepare} onClick={onPrepare}>
                  按确认方案转换配体
                </ActionButton>
              )}
            </div>
          ) : null}
        </>
      ) : null}

      {evidence ? (
        <section className="macrocycle-evidence" aria-labelledby="macrocycle-evidence-title">
          <header>
            <div>
              <strong id="macrocycle-evidence-title">最近一次大环准备证据</strong>
              <span>{evidence.reviewId || "未记录审查编号"}</span>
            </div>
            <StatusBadge tone={evidence.bondsMatch === false ? "error" : evidence.bondsMatch ? "ok" : "muted"}>
              {evidence.bondsMatch === false ? "键不一致" : evidence.bondsMatch ? "键已核对" : "证据待核对"}
            </StatusBadge>
          </header>
          <dl>
            <div><dt>处理方式</dt><dd>{evidence.selectionMode === "rigid" ? "刚性大环" : "受审查断环"}</dd></div>
            <div><dt>预期断环键（1-based）</dt><dd>{formatZeroBasedBondPairs(evidence.expectedBonds)}</dd></div>
            <div><dt>实际断环键（1-based）</dt><dd>{evidence.actualBonds.length ? formatZeroBasedBondPairs(evidence.actualBonds) : "未记录"}</dd></div>
            <div><dt>G* 伪原子</dt><dd>{evidence.gluePseudoAtomCount ?? "未记录"}</dd></div>
            <div><dt>Meeko</dt><dd>{evidence.meekoVersion || "未记录"}</dd></div>
            <div><dt>RDKit</dt><dd>{evidence.rdkitVersion || "未记录"}</dd></div>
          </dl>
        </section>
      ) : null}
    </section>
  );
}
