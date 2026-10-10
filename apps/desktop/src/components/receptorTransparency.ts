export const DEFAULT_RECEPTOR_TRANSPARENCY = 26;
export const DEFAULT_REPORT_RECEPTOR_TRANSPARENCY = 78;

/** UI percentage: 0 is opaque, 100 is invisible; geometry is untouched. */
export function normalizeReceptorTransparency(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_RECEPTOR_TRANSPARENCY;
  return Math.min(100, Math.max(0, Math.round(value)));
}

export function receptorOpacity(transparency: number): number {
  return (100 - normalizeReceptorTransparency(transparency)) / 100;
}

export function workspaceReceptorStyle(opacity: number, stickRadius = 0.1) {
  return {
    cartoon: { color: "spectrum", opacity },
    stick: { radius: stickRadius, colorscheme: "Jmol", opacity },
  };
}
