import { MouseScroll } from "@phosphor-icons/react";
import type { DockStartProject } from "../types";
import StatusBadge from "./StatusBadge";

export type RunBoxFieldKey = keyof DockStartProject["box"];
export type RunBoxWheelStep = 0.1 | 1 | 5;
export type RunBoxLineThickness = "thin" | "standard" | "bold";
export type RunAxisSpacing = "compact" | "standard" | "wide";

type RunBoxInspectorProps = {
  boxForm: Record<RunBoxFieldKey, string>;
  volume: number;
  wheelBinding: RunBoxFieldKey | null;
  wheelStep: RunBoxWheelStep;
  boxLineThickness: RunBoxLineThickness;
  axisSpacing: RunAxisSpacing;
  disabled?: boolean;
  idPrefix?: string;
  className?: string;
  onFieldChange: (key: RunBoxFieldKey, value: string) => void;
  onWheelBindingChange: (key: RunBoxFieldKey | null) => void;
  onWheelStepChange: (step: RunBoxWheelStep) => void;
  onBoxLineThicknessChange: (value: RunBoxLineThickness) => void;
  onAxisSpacingChange: (value: RunAxisSpacing) => void;
};

const boxFields: Array<{ key: RunBoxFieldKey; label: string }> = [
  { key: "center_x", label: "中心 X" },
  { key: "size_x", label: "尺寸 X" },
  { key: "center_y", label: "中心 Y" },
  { key: "size_y", label: "尺寸 Y" },
  { key: "center_z", label: "中心 Z" },
  { key: "size_z", label: "尺寸 Z" },
];

const boxWheelSteps: Array<{ value: RunBoxWheelStep; label: string }> = [
  { value: 0.1, label: "细调" },
  { value: 1, label: "常规" },
  { value: 5, label: "快速" },
];

const boxLineOptions: Array<{ value: RunBoxLineThickness; label: string }> = [
  { value: "thin", label: "细" },
  { value: "standard", label: "标准" },
  { value: "bold", label: "粗" },
];

const axisSpacingOptions: Array<{ value: RunAxisSpacing; label: string }> = [
  { value: "compact", label: "紧凑" },
  { value: "standard", label: "标准" },
  { value: "wide", label: "展开" },
];

export const runBoxFieldLabels: Record<RunBoxFieldKey, string> = Object.fromEntries(
  boxFields.map((field) => [field.key, field.label]),
) as Record<RunBoxFieldKey, string>;

export default function RunBoxInspector({
  boxForm,
  volume,
  wheelBinding,
  wheelStep,
  boxLineThickness,
  axisSpacing,
  disabled = false,
  idPrefix = "run-box",
  className = "",
  onFieldChange,
  onWheelBindingChange,
  onWheelStepChange,
  onBoxLineThicknessChange,
  onAxisSpacingChange,
}: RunBoxInspectorProps) {
  return (
    <aside className={`run-box-inspector ${className}`.trim()} aria-label="搜索范围参数">
      <div className="run-inspector-title">
        <div>
          <span>Docking box</span>
          <strong>{volume.toLocaleString("zh-CN", { maximumFractionDigits: 1 })} Å³</strong>
        </div>
        <StatusBadge tone={volume > 27000 ? "warning" : "ok"}>
          {volume > 27000 ? "范围偏大" : "范围可用"}
        </StatusBadge>
      </div>

      <div className="run-box-wheel-toolbar">
        <div className="run-box-step-group" aria-label="滚轮调整步进">
          {boxWheelSteps.map((step) => (
            <button
              type="button"
              key={step.value}
              disabled={disabled}
              className={wheelStep === step.value ? "is-active" : ""}
              aria-pressed={wheelStep === step.value}
              onClick={() => onWheelStepChange(step.value)}
            >
              {step.label}<small>{step.value} Å</small>
            </button>
          ))}
        </div>
        <p className={wheelBinding ? "is-bound" : ""} aria-live="polite">
          <MouseScroll aria-hidden="true" size={15} />
          {wheelBinding ? `滚轮调整${runBoxFieldLabels[wheelBinding]}` : "未绑定时滚轮缩放视图"}
        </p>
      </div>

      <div className="run-box-display-controls" aria-label="三维显示清晰度">
        <div>
          <span>Box 线条</span>
          <div className="run-box-display-segment">
            {boxLineOptions.map((option) => (
              <button
                type="button"
                key={option.value}
                disabled={disabled}
                className={boxLineThickness === option.value ? "is-active" : ""}
                aria-pressed={boxLineThickness === option.value}
                onClick={() => onBoxLineThicknessChange(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        <div>
          <span>坐标轴间距</span>
          <div className="run-box-display-segment">
            {axisSpacingOptions.map((option) => (
              <button
                type="button"
                key={option.value}
                disabled={disabled}
                className={axisSpacing === option.value ? "is-active" : ""}
                aria-pressed={axisSpacing === option.value}
                onClick={() => onAxisSpacingChange(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="run-box-fields">
        {boxFields.map((field) => {
          const invalid = !Number.isFinite(Number(boxForm[field.key]))
            || (field.key.startsWith("size_") && Number(boxForm[field.key]) <= 0);
          const isBound = wheelBinding === field.key;
          const inputId = `${idPrefix}-${field.key}`;
          return (
            <div key={field.key} className={`run-box-field ${invalid ? "is-invalid" : ""} ${isBound ? "is-bound" : ""}`.trim()}>
              <div className="run-box-field-heading">
                <label htmlFor={inputId}>{field.label} <small>Å</small></label>
                <button
                  type="button"
                  disabled={disabled}
                  aria-pressed={isBound}
                  aria-label={`${isBound ? "解除" : "绑定"}${field.label}的鼠标滚轮调整`}
                  onClick={() => onWheelBindingChange(isBound ? null : field.key)}
                >
                  <MouseScroll aria-hidden="true" size={13} />
                  {isBound ? "已绑定" : "绑定"}
                </button>
              </div>
              <input
                id={inputId}
                disabled={disabled}
                value={boxForm[field.key]}
                inputMode="decimal"
                onChange={(event) => onFieldChange(field.key, event.target.value)}
                aria-invalid={invalid}
              />
            </div>
          );
        })}
      </div>

      <p>搜索范围只定义 Vina 的探索空间，不代表自动识别了真实结合口袋。</p>
    </aside>
  );
}
