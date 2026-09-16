export type TooltipAvailability = {
  disabled: boolean;
  childDisabled: boolean;
  label: string;
};

export function isTooltipAvailable({
  disabled,
  childDisabled,
  label,
}: TooltipAvailability): boolean {
  return !disabled && !childDisabled && label.trim().length > 0;
}

export function mergeAriaDescribedBy(
  current: string | undefined,
  tooltipId: string,
): string {
  const values = (current ?? "").split(/\s+/).filter(Boolean);
  if (!values.includes(tooltipId)) values.push(tooltipId);
  return values.join(" ");
}

/**
 * “?”提示图标的无障碍名称。带主语时说明该提示解释的是哪个参数，
 * 便于键盘/读屏用户在不悬停的情况下也能知道提示的用途。
 */
export function buildFieldHintLabel(subject: string): string {
  const trimmed = subject.trim();
  return trimmed ? `查看“${trimmed}”的说明` : "查看参数说明";
}
