export type ThreeDmolModule = typeof import("3dmol");
export type ThreeDmolViewer = ReturnType<ThreeDmolModule["createViewer"]>;
export type ThreeDmolModel = ReturnType<ThreeDmolViewer["addModel"]>;

let modulePromise: Promise<ThreeDmolModule> | null = null;

/** Keep 3Dmol outside the initial UI chunk and coalesce concurrent viewers. */
export function load3Dmol(): Promise<ThreeDmolModule> {
  if (!modulePromise) modulePromise = import("3dmol");
  return modulePromise;
}

export function structureFingerprint(content: string, format: string, identity = ""): string {
  if (!content) return `${identity}|${format}|0`;
  const middle = Math.floor(content.length / 2);
  return [
    identity,
    format,
    content.length,
    content.slice(0, 96),
    content.slice(Math.max(0, middle - 48), middle + 48),
    content.slice(-96),
  ].join("|");
}
