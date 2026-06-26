export type ToolStatus = "ok" | "missing" | "error" | "unknown";

export type ToolSource =
  | "bundled"
  | "configured"
  | "auto"
  | "current_environment"
  | "frontend_dependency"
  | "missing"
  | "unknown";

export type ToolCheckResult = {
  key: string;
  name: string;
  status: ToolStatus;
  version: string;
  path: string;
  message: string;
  raw_error: string;
  source: ToolSource;
  bundled_path: string;
  is_bundled: boolean;
};

export type PreparationCapabilityDetail = {
  status: ToolStatus;
  message: string;
  raw_error?: string;
  api_candidates_found?: string[];
  cli_candidates_found?: string[];
  [key: string]: unknown;
};

export type PreparationToolCapabilityResult = {
  key: string;
  name: string;
  status: ToolStatus;
  version: string;
  path: string;
  python_path: string;
  python_source: ToolSource;
  message: string;
  raw_error: string;
  source: ToolSource;
  capabilities: Record<string, PreparationCapabilityDetail>;
};

export type PreparationToolStatusResponse = {
  ok: boolean;
  project_dir: string;
  tools?: {
    python?: ToolCheckResult;
    rdkit?: PreparationToolCapabilityResult;
    meeko?: PreparationToolCapabilityResult;
  };
  python_path?: string;
  python_source?: ToolSource;
  message?: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  };
};

export type ToolchainFullStatus = "partial" | "ready" | "missing";
export type ToolchainRuntimeMode = "dev" | "packaged" | "unknown";
export type BundledPackageStatus = "ready" | "incomplete" | "missing";
export type BundledVinaPackageStatus = BundledPackageStatus;

export type BundledBinaryIntegrity = {
  status: BundledPackageStatus;
  binary_path: string;
  binary_exists: boolean;
  sha256: string;
  manifest_sha256: string;
  sha256_matches: boolean;
  manifest_bundled: boolean;
  manifest_version: string;
  manifest_source: string;
  manifest_prepared_at: string;
  warnings: string[];
  message: string;
};

export type BundledVinaIntegrity = BundledBinaryIntegrity & {
  license_path: string;
  license_exists: boolean;
  third_party_notices_path: string;
  third_party_notices_exists: boolean;
  third_party_notices_has_autodock_vina: boolean;
};

export type BundledPythonIntegrity = BundledBinaryIntegrity;

export type BundledPackageCheck<TIntegrity extends BundledBinaryIntegrity> = {
  ok: boolean;
  status: BundledPackageStatus;
  integrity: TIntegrity;
  warnings: string[];
  message: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type ToolchainStatusResponse = {
  ok: boolean;
  runtime_mode: ToolchainRuntimeMode;
  resource_dir: string;
  toolchain_root: string;
  tools_dir: string;
  licenses_dir: string;
  manifest_file: string;
  manifest_exists: boolean;
  manifest: Record<string, unknown>;
  manifest_error: string;
  bundled_vina: {
    exists: boolean;
    path: string;
    version: string;
    status: ToolStatus;
    message: string;
    raw_error: string;
    sha256: string;
    package_status: BundledPackageStatus;
  };
  bundled_vina_integrity: BundledVinaIntegrity | null;
  bundled_vina_package: BundledPackageCheck<BundledVinaIntegrity> | null;
  bundled_python: {
    exists: boolean;
    path: string;
    version: string;
    status: ToolStatus;
    message: string;
    raw_error: string;
    sha256: string;
    package_status: BundledPackageStatus;
  };
  bundled_python_integrity: BundledPythonIntegrity | null;
  bundled_python_package: BundledPackageCheck<BundledPythonIntegrity> | null;
  warnings: string[];
  active_vina: ToolCheckResult | null;
  active_source: ToolSource;
  resolved_python: ToolCheckResult | null;
  python_source: ToolSource;
  meeko_for_python: ToolCheckResult | null;
  rdkit_for_python: ToolCheckResult | null;
  meeko_python_source: ToolSource;
  rdkit_python_source: ToolSource;
  licenses: {
    exists: boolean;
    third_party_notices: string;
    third_party_notices_exists: boolean;
  };
  resources: {
    exists: boolean;
    tools_dir_exists: boolean;
    vina_dir_exists: boolean;
    python_dir_exists: boolean;
  };
  full_status: ToolchainFullStatus;
  message: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  };
};

export type RunCheckResult = {
  key: string;
  name: string;
  status: ToolStatus;
  message: string;
  path?: string;
  version?: string;
  raw_error?: string;
};

export type RunFileStatus = {
  key: string;
  name: string;
  path: string;
  exists: boolean;
  is_file: boolean;
  size: number;
  non_empty: boolean;
  status: "ok" | "missing" | "empty" | "error";
  message: string;
  raw_error?: string;
};

export type RawStructureStatus = RunFileStatus & {
  source: string;
  source_id: string;
  query_type: string;
  downloaded_at: string;
  raw_file: string;
  size_bytes: number;
  modified_at: string;
  absolute_path: string;
  record_consistent: boolean;
};

export type ViewerFileKind =
  | "receptor_raw"
  | "ligand_raw"
  | "receptor_prepared"
  | "ligand_prepared"
  | "docking_output";

export type ViewerStructureResult = {
  ok: boolean;
  file_kind: ViewerFileKind | string;
  relative_path: string;
  absolute_path: string;
  exists: boolean;
  format: string;
  content: string;
  size_bytes: number;
  message: string;
  warnings: string[];
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
  run_id?: string;
  mode?: number;
  score?: PoseScoreSummary | null;
};

export type PoseScoreSummary = {
  mode: number;
  affinity_kcal_mol: number | null;
  rmsd_lb: number | null;
  rmsd_ub: number | null;
};

export type DockingPoseSummary = PoseScoreSummary & {
  relative_path: string;
  size_bytes: number;
  line_count: number;
  message: string;
  warnings: string[];
};

export type DockingPoseListResponse = {
  ok: boolean;
  project_dir: string;
  run_id: string;
  relative_path: string;
  format: string;
  poses: DockingPoseSummary[];
  scores_file: string;
  message: string;
  warnings: string[];
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type ViewerFileStatusResponse = {
  ok: boolean;
  project_dir: string;
  files: Record<ViewerFileKind, ViewerStructureResult>;
  docking_outputs: Array<ViewerStructureResult & { run_id?: string; run_status?: string }>;
  message: string;
  warnings: string[];
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type BoxVisualizationPayload = {
  center_x: number;
  center_y: number;
  center_z: number;
  size_x: number;
  size_y: number;
  size_z: number;
  unit: "angstrom";
  min: { x: number; y: number; z: number };
  max: { x: number; y: number; z: number };
  corners: Array<{ x: number; y: number; z: number }>;
  viewer_box_payload: {
    center: { x: number; y: number; z: number };
    dimensions: { w: number; h: number; d: number };
    color: string;
    alpha: number;
    wireframe: boolean;
  };
};

export type BoxVisualizationResponse = {
  ok: boolean;
  project_dir: string;
  project?: DockStartProject | null;
  box: DockStartProject["box"];
  visualization: BoxVisualizationPayload;
  warnings: string[];
  message: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type ScoreRow = {
  mode: number;
  affinity_kcal_mol: number;
  rmsd_lb: number;
  rmsd_ub: number;
};

export type DockStartSettings = {
  tool_paths: {
    vina: string;
    python: string;
  };
  project: {
    default_project_dir: string;
  };
};

export type SettingsResponse = {
  ok: boolean;
  settings_path: string;
  settings: DockStartSettings | null;
  error?: {
    message: string;
    raw_error: string;
  };
};

export type ProjectFileRef = {
  source: string;
  source_id: string;
  query_type: string;
  downloaded_at: string;
  raw_file: string;
  file: string;
};

export type PreparationStatus = "not_started" | "checking" | "ready" | "running" | "finished" | "failed";
export type PreparationTarget = "receptor" | "ligand";
export type PreparationMethod = "meeko" | "rdkit_meeko" | "external_manual";

export type PreparationResult = {
  target: PreparationTarget;
  status: PreparationStatus;
  method: PreparationMethod | null;
  input_file: string | null;
  output_file: string;
  started_at: string | null;
  finished_at: string | null;
  python_path: string;
  python_source: string;
  rdkit_available: boolean;
  meeko_available: boolean;
  command: string[];
  stdout_file: string;
  stderr_file: string;
  log_file: string;
  error: {
    code?: string;
    message?: string;
    raw_error?: string;
    suggestion?: string;
  } | null;
  warnings: string[];
};

export type PreparationState = {
  receptor: PreparationResult;
  ligand: PreparationResult;
};

export type DockStartProject = {
  project_name: string;
  created_at: string;
  updated_at: string;
  project_dir: string;
  receptor: ProjectFileRef;
  ligand: ProjectFileRef;
  box: {
    center_x: number;
    center_y: number;
    center_z: number;
    size_x: number;
    size_y: number;
    size_z: number;
  };
  vina: {
    exhaustiveness: number;
    num_modes: number;
    energy_range: number;
    cpu: number;
    seed: number | null;
  };
  config: {
    vina_config_file: string;
    generated_at: string;
  };
  preparation: PreparationState;
  runs: Array<Record<string, unknown>>;
};

export type PreparationStatusResponse = {
  ok: boolean;
  project_dir: string;
  project: DockStartProject | null;
  preparation: PreparationState | null;
  tools?: {
    python?: ToolCheckResult;
    rdkit?: PreparationToolCapabilityResult;
    meeko?: PreparationToolCapabilityResult;
  };
  files?: {
    receptor_raw?: RunFileStatus;
    ligand_raw?: RunFileStatus;
    receptor_prepared?: RunFileStatus;
    ligand_prepared?: RunFileStatus;
  };
  target?: PreparationTarget;
  ready?: boolean;
  missing_tools?: string[];
  message?: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  };
};

export type ProjectResponse = {
  ok: boolean;
  project_dir?: string;
  project: DockStartProject | null;
  box?: DockStartProject["box"];
  vina?: DockStartProject["vina"];
  config_file?: string;
  config_text?: string;
  checks?: RunCheckResult[];
  next_run_id?: string;
  run_id?: string;
  metadata?: Record<string, unknown>;
  metadata_file?: string;
  command_preview_file?: string;
  config_snapshot_file?: string;
  stdout_file?: string;
  stderr_file?: string;
  output_file?: string;
  log_file?: string;
  scores?: ScoreRow[];
  scores_file?: string;
  project_scores_file?: string;
  best_affinity?: number | null;
  analyzed_at?: string;
  report_file?: string;
  project_report_file?: string;
  reported_at?: string;
  raw_file?: string;
  source?: string;
  source_id?: string;
  query_type?: string;
  format?: string;
  url?: string;
  receptor?: RawStructureStatus;
  ligand?: RawStructureStatus;
  deleted_file?: string;
  report_status?: string;
  scores_status?: RunFileStatus | null;
  can_export?: boolean;
  files?: RunFileStatus[];
  command?: string[];
  command_preview?: string;
  warnings?: string[];
  message?: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  };
};
