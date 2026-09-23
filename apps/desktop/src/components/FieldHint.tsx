import { Question } from "@phosphor-icons/react";
import Tooltip from "./Tooltip";
import { buildFieldHintLabel } from "./tooltipAccessibility";

type FieldHintProps = {
  /** 悬浮/聚焦时展示的完整解释文本。 */
  label: string;
  /** 无障碍名称里的主语，例如“搜索彻底程度”，用于“查看 X 的说明”。 */
  subject?: string;
  /** 提示相对“?”图标的出现方向。默认向右，避免遮挡同一字段下方的输入框。 */
  placement?: "right" | "bottom";
  className?: string;
};

/**
 * 参数旁的“?”提示图标。
 *
 * 说明文字默认不展开，鼠标悬停或键盘聚焦“?”时才展示。内部复用现有的
 * Tooltip 组件（含 portal 定位、视口钳制、Escape 关闭与 aria-describedby），
 * 只负责提供一致的“?”按钮外观与无障碍名称，不重复实现提示逻辑。
 */
export default function FieldHint({
  label,
  subject = "",
  placement = "right",
  className = "",
}: FieldHintProps) {
  const accessibleLabel = buildFieldHintLabel(subject);
  return (
    <Tooltip className={`ds-field-hint-tooltip ${className}`.trim()} label={label} placement={placement}>
      <button aria-label={accessibleLabel} className="ds-field-hint" type="button">
        <Question aria-hidden="true" size={15} weight="regular" />
      </button>
    </Tooltip>
  );
}
