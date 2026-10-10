/** Geometry-only figure styling; this radius is not an interaction cutoff. */
export const REPORT_POCKET_RADIUS_ANGSTROM = 5;

type FigureAtom = { x?: number; y?: number; z?: number; elem?: string; index?: number };
type PositionedAtom = FigureAtom & { x: number; y: number; z: number };

export function reportPocketAtomIndices(
  receptor: readonly FigureAtom[], ligand: readonly FigureAtom[],
  radius = REPORT_POCKET_RADIUS_ANGSTROM,
): number[] {
  const isHeavy = (atom: FigureAtom) => !["H", "D", "HD", "HS"].includes((atom.elem ?? "").toUpperCase());
  const isHeavyPositioned = (atom: FigureAtom): atom is PositionedAtom => isHeavy(atom)
    && [atom.x, atom.y, atom.z].every(value => typeof value === "number" && Number.isFinite(value));
  const heavyLigand = ligand.filter(isHeavyPositioned);
  if (!Number.isFinite(radius) || radius <= 0 || !heavyLigand.length) return [];
  const squaredRadius = radius * radius;
  return receptor.filter(isHeavyPositioned).filter(atom => Number.isInteger(atom.index)
    && heavyLigand.some(target => (atom.x - target.x) ** 2 + (atom.y - target.y) ** 2
      + (atom.z - target.z) ** 2 <= squaredRadius)).map(atom => atom.index!);
}

export function reportReceptorStyle(color: string, opacity = 0.22) {
  return { cartoon: { color, opacity } };
}

export function reportPocketStyle(color: string, opacity = 0.32) {
  return { stick: { color, radius: 0.1, opacity } };
}

export function reportLigandStyle() {
  return {
    stick: { radius: 0.24, colorscheme: "purpleCarbon" },
    sphere: { scale: 0.19, colorscheme: "purpleCarbon" },
  };
}
