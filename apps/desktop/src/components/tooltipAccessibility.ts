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
