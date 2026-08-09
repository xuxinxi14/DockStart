import type { DockStartProject } from "../types";

/**
 * Identity for every project field that can invalidate a generated/imported
 * map set or its user-facing attestation. `revision` is the authoritative
 * project commit counter; the explicit fields keep this useful for migrated
 * projects that predate that counter.
 */
export function mapsProjectContextKey(project: DockStartProject): string {
  const protocol = project.docking_protocol;
  return JSON.stringify([
    project.project_dir,
    project.revision ?? 0,
    project.updated_at ?? "",
    project.receptor?.file ?? "",
    project.receptor?.raw_file ?? "",
    project.box.center_x,
    project.box.center_y,
    project.box.center_z,
    project.box.size_x,
    project.box.size_y,
    project.box.size_z,
    project.vina.scoring,
    project.vina.spacing,
    protocol?.engine ?? "vina",
    protocol?.protocol_id ?? "",
    protocol?.receptor_mode ?? protocol?.mode ?? "rigid",
    protocol?.grid_source ?? "receptor",
    protocol?.map_set_id ?? protocol?.active_map_set_id ?? "",
  ]);
}
