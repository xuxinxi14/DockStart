export type StructureInputRole = "receptor" | "ligand";
export type StructureInputKind = "pdbqt" | "raw" | "unsupported";

const RECEPTOR_RAW_EXTENSIONS = new Set(["pdb", "cif"]);
const LIGAND_RAW_EXTENSIONS = new Set(["sdf", "mol"]);

export function structureInputExtension(path: string): string {
  const fileName = path.trim().split(/[\\/]/).pop() ?? "";
  const dotIndex = fileName.lastIndexOf(".");
  return dotIndex >= 0 ? fileName.slice(dotIndex + 1).toLowerCase() : "";
}

export function structureInputKind(path: string, role: StructureInputRole): StructureInputKind {
  const extension = structureInputExtension(path);
  if (extension === "pdbqt") return "pdbqt";
  const rawExtensions = role === "receptor" ? RECEPTOR_RAW_EXTENSIONS : LIGAND_RAW_EXTENSIONS;
  return rawExtensions.has(extension) ? "raw" : "unsupported";
}

export function splitLigandStructurePaths(paths: string[]): { pdbqt: string[]; raw: string[] } {
  return paths.reduce<{ pdbqt: string[]; raw: string[] }>((result, path) => {
    const kind = structureInputKind(path, "ligand");
    if (kind === "pdbqt") result.pdbqt.push(path);
    if (kind === "raw") result.raw.push(path);
    return result;
  }, { pdbqt: [], raw: [] });
}

