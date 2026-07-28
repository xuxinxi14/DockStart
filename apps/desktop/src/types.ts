export type ToolStatus = "ok" | "missing" | "error" | "unknown";

export type ToolSource =
  | "bundled"
  | "configured"
  | "auto"
  | "current_environment"
  | "frontend_dependency"
  | "missing"
  | "unknown";

export type VinaCliFeatureCapability = {
  option: string;
  status: "supported" | "unsupported" | "unknown";
  supported: boolean | null;
  advertised: boolean | null;
  minimum_version: string;
  version_compatible: boolean | null;
  message: string;
};

export type VinaCliCapabilities = {
  status: "ok" | "partial" | "unknown";
  source: "help_advanced";
  checked: boolean;
  version: string;
  help_exit_code: number | null;
  help_sha256: string;
  features: {
    multiple_ligands?: VinaCliFeatureCapability;
    maps?: VinaCliFeatureCapability;
    write_maps?: VinaCliFeatureCapability;
    autobox: VinaCliFeatureCapability;
    no_refine: VinaCliFeatureCapability;
    force_even_voxels: VinaCliFeatureCapability;
    unbound_energy: VinaCliFeatureCapability;
  };
  message: string;
  raw_error: string;
};

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
  ad4zn_compatible?: boolean;
  ad4zn_minimum_version?: string;
  capabilities?: VinaCliCapabilities;
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
  first_run_guidance?: {
    status: string;
    recommended_action: string;
    primary_page: string;
    message: string;
  };
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

export type UsageMode = "basic" | "assisted" | "demo" | "setup";

export type CapabilityBlockingItem = {
  mode: UsageMode | string;
  item: string;
  message: string;
};

export type AppCapabilityProfile = {
  ok: boolean;
  app_version: string;
  vina_status: ToolCheckResult | null;
  python_status: ToolCheckResult | null;
  rdkit_status: ToolCheckResult | null;
  meeko_status: ToolCheckResult | null;
  viewer_status: ToolCheckResult | null;
  basic_mode_available: boolean;
  assisted_mode_available: boolean;
  demo_mode_available: boolean;
  recommended_mode: UsageMode | string;
  blocking_items: CapabilityBlockingItem[];
  next_action: string;
  demo_projects: Array<{
    name: string;
    project_dir: string;
    project_json: string;
    exists: boolean;
  }>;
  message: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type MinimumRequirementsStatus = {
  ok: boolean;
  project_dir: string;
  project: DockStartProject | null;
  basic_mode: {
    available: boolean;
    files_ready: boolean;
    ready: boolean;
  };
  assisted_mode: {
    available: boolean;
    raw_inputs_ready: boolean;
    ready: boolean;
  };
  demo_mode: {
    available: boolean;
    projects: AppCapabilityProfile["demo_projects"];
  };
  missing_items: CapabilityBlockingItem[];
  next_action: string;
  workflow?: ProjectWorkflowStatusResponse;
  message: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type ProjectModeRecommendation = {
  ok: boolean;
  project_dir: string;
  project: DockStartProject | null;
  recommended_mode: UsageMode | string;
  reason: string;
  next_action: string;
  minimum_requirements: MinimumRequirementsStatus;
  message: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type ToolchainRepairSuggestion = {
  issue: string;
  severity: "info" | "warning" | "error" | string;
  affected_mode: string;
  explanation: string;
  recommended_fix: string;
  documentation_link: string;
  copyable_commands: string[];
  manual_steps: string[];
};

export type ToolchainRepairSuggestionsResponse = {
  ok: boolean;
  suggestions: ToolchainRepairSuggestion[];
  message: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type PostInstallCheckResponse = {
  ok: boolean;
  generated_at: string;
  app_version: string;
  os: {
    system: string;
    release: string;
    version: string;
    machine: string;
  };
  runtime_mode: string;
  release_build_mode: string;
  paths: {
    settings_path: string;
    resource_dir: string;
    toolchain_root: string;
  };
  tools: Record<string, {
    status: ToolStatus | string;
    source: ToolSource | string;
    version: string;
    path: string;
    message: string;
  }>;
  demo_projects: {
    ok: boolean;
    available: boolean;
    count: number;
    demos: Array<Record<string, unknown>>;
  };
  modes: {
    basic_mode_available: boolean;
    assisted_mode_available: boolean;
    demo_mode_available: boolean;
    recommended_mode: UsageMode | string;
    next_action: string;
  };
  issues: string[];
  privacy_note: string;
  message: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type DiagnosticReportResponse = {
  ok: boolean;
  report_file: string;
  generated_at: string;
  check: PostInstallCheckResponse;
  message: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type DemoProjectSummary = {
  demo_type: string;
  id: string;
  title: string;
  description: string;
  mode: string;
  required_tools: string[];
  tags: string[];
  entry_step: string;
  entry_run_id: string;
  button_label: string;
  target_name_prefix: string;
  template_dir: string;
  manifest: string;
  project_json: string;
  exists: boolean;
  missing_files: string[];
  size_bytes: number;
  readme: string;
  files: Record<string, unknown>;
  note: string;
  disclaimer: string;
};

export type DemoProjectsResponse = {
  ok: boolean;
  examples_root: string;
  demos: DemoProjectSummary[];
  message: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
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

export type RunPreflightStatus = "ok" | "warning" | "error" | "missing" | "unknown";

export type RunPreflightCheck = {
  key: string;
  name: string;
  status: RunPreflightStatus;
  message: string;
  detail?: string;
  blocking: boolean;
  action_page?: string;
  path?: string;
  version?: string;
};

export type PdbqtInputStats = {
  relative_path: string;
  absolute_path: string;
  size_bytes: number;
  sha256: string;
  atom_count: number;
  coordinate_count: number;
  coordinate_bounds: {
    min: { x: number; y: number; z: number };
    max: { x: number; y: number; z: number };
  } | null;
  coordinate_center: { x: number; y: number; z: number } | null;
  chains: string[];
  atom_types: string[];
  torsdof?: number | null;
};

export type RunHistoryItem = {
  run_id: string;
  status: string;
  created_at: string;
  started_at: string;
  finished_at: string;
  duration_seconds: number | null;
  best_affinity: number | null;
  primary_score_kcal_mol?: number | null;
  stage: string;
  run_mode?: VinaRunMode;
  scoring_protocol?: "vina" | "ad4_maps";
  scoring_function?: "vina" | "vinardo" | "ad4" | string;
};

export type ProjectRunGuardItem = {
  run_id: string;
  status: string;
  stage: string;
  process_active: boolean;
  executor_active: boolean;
  can_cancel: boolean;
  registry_task_id: string;
  registry_status: string;
  message: string;
};

export type ProjectRunGuardPayload = {
  ok: boolean;
  blocked: boolean;
  project_dir: string;
  active_runs: ProjectRunGuardItem[];
  message: string;
  error: string;
};

export type StructureReviewCheck = {
  key: string;
  role: "receptor" | "ligand" | string;
  name: string;
  status: "ok" | "warning" | "unknown" | string;
  message: string;
  detail: string;
  evidence: string;
  blocking: false;
  requires_manual_review: boolean;
};

export type CoordinateBounds = {
  x: [number, number];
  y: [number, number];
  z: [number, number];
};

export type ReceptorPdbqtFacts = {
  format?: "pdbqt";
  atom_count?: number;
  heavy_atom_count?: number;
  hydrogen_atom_count?: number;
  coordinate_count?: number;
  has_3d_coordinates?: boolean;
  coordinate_bounds?: CoordinateBounds | null;
  chains?: string[];
  residue_count?: number;
  model_count?: number;
  autodock_atom_types?: string[];
  partial_charge_count?: number;
  partial_charge_sum?: number | null;
  receptor_pdbqt_mode?: "rigid" | "flexible" | "unknown";
  active_torsions?: number | null;
  activity_torsion_applicable?: boolean;
  flexible_residue_count?: number;
  branch_count?: number;
  fact_sources?: Record<string, "最终 PDBQT">;
};

export type ReceptorRawFacts = {
  atom_count?: number;
  heavy_atom_count?: number;
  hydrogen_atom_count?: number;
  coordinate_count?: number;
  has_3d_coordinates?: boolean;
  coordinate_bounds?: CoordinateBounds | null;
  chains?: string[];
  residue_count?: number;
  ion_non_polymer_components?: Array<Record<string, unknown>>;
  alternate_locations?: string[];
  residue_template_anomalies?: Array<Record<string, unknown>> | null;
  residue_template_check_status?: "not_run" | "passed" | "warning" | "failed";
  nonstandard_chirality_geometry?: null;
  fact_sources?: Record<string, "原始结构" | "RDKit" | "Meeko" | "最终 PDBQT" | "准备快照">;
  [key: string]: unknown;
};

export type ReceptorStructureReview = ReceptorPdbqtFacts & {
  raw_file?: string;
  prepared_file?: string;
  source_file?: string;
  source_kind?: "raw" | "prepared" | "missing";
  raw?: ReceptorRawFacts;
  pdbqt?: ReceptorPdbqtFacts;
};

export type StructureReviewPayload = {
  scientific_validation: false;
  disclaimer: string;
  receptor: ReceptorStructureReview;
  ligand: Record<string, unknown>;
  provenance: Record<string, unknown>;
  checks: StructureReviewCheck[];
  warning_count: number;
  unknown_count: number;
};

export type RunPreflightResponse = {
  ok: boolean;
  ready: boolean;
  project_dir: string;
  project: DockStartProject | null;
  checks: RunPreflightCheck[];
  blockers: string[];
  warnings: string[];
  input_stats: {
    receptor: PdbqtInputStats | null;
    ligand: PdbqtInputStats | null;
  };
  structure_review?: StructureReviewPayload;
  box: DockStartProject["box"] & {
    volume_angstrom3: number;
    warnings: string[];
  };
  vina_params: DockStartProject["vina"];
  tool: {
    status: ToolStatus | string;
    version: string;
    path: string;
    source: ToolSource | string;
    message: string;
    capabilities?: VinaCliCapabilities;
  };
  output: {
    runs_dir: string;
    writable: boolean;
    free_bytes: number;
  };
  system: {
    system: string;
    release: string;
    machine: string;
    cpu_count: number;
    memory_bytes: number;
    fingerprint: string;
  };
  estimate: {
    available: boolean;
    sample_count: number;
    range_label: string;
    message: string;
  };
  next_run_id: string;
  command_preview: string;
  run_history: RunHistoryItem[];
  scoring_protocol?: "vina" | "ad4_maps";
  run_mode?: VinaRunMode;
  autobox?: boolean;
  pose_input_attestation?: PoseInputAttestationStatus;
  ad4_maps?: AutoGridMapsStatusResponse | null;
  active_run_guard?: ProjectRunGuardPayload;
  message: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type RunRuntimeStatusResponse = {
  ok: boolean;
  project_dir: string;
  project: DockStartProject | null;
  run_id: string;
  metadata: Record<string, unknown> | null;
  progress: {
    percent: number;
    message: string;
  };
  stage: string;
  elapsed_seconds: number;
  stdout_tail: string;
  stderr_tail: string;
  log_tail: string;
  message: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
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

export type StructureSearchSelection = {
  download_command: "fetch-pdb" | "fetch-pubchem" | string;
  pdb_id?: string;
  query?: string;
  query_type: "pdb_id" | "cid" | "name" | string;
  format: "pdb" | "cif" | "sdf" | string;
};

export type StructureSearchCandidate = {
  candidate_id: string;
  provider: "rcsb" | "pubchem" | string;
  source_id: string;
  title: string;
  subtitle: string;
  metadata: Record<string, unknown>;
  selection: StructureSearchSelection;
};

export type StructureSearchResponse = {
  ok: boolean;
  provider: "rcsb" | "pubchem" | string;
  query: string;
  query_type: string;
  requested_limit: number;
  total_count: number;
  returned_count: number;
  truncated: boolean;
  selection_required: boolean;
  candidates: StructureSearchCandidate[];
  message: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type CandidateStructurePreviewResponse = {
  ok: boolean;
  provider: "rcsb" | "pubchem" | string;
  source_id: string;
  format: "pdb" | "cif" | "sdf" | string;
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
  pose_kind?: "input" | "optimized" | string;
  pose_label?: string;
  score?: PoseScoreSummary | null;
};

export type MultipleLigandMemberSummary = {
  member_index: number;
  member_id?: string;
  display_name?: string;
  source_name?: string;
  source_file?: string;
  source_sha256?: string;
};

export type MultipleLigandPoseViewerResult = {
  ok: boolean;
  project_dir?: string;
  run_id: string;
  protocol_id?: "simultaneous_multi_ligand" | string;
  mode: number;
  member_index: number;
  member?: MultipleLigandMemberSummary | null;
  receptor?: ViewerStructureResult | null;
  pose?: ViewerStructureResult | null;
  available_modes?: number[];
  joint_affinity_kcal_mol?: number | null;
  warnings?: string[];
  message: string;
  error?: {
    code: string;
    message: string;
    raw_error?: string;
    suggestion?: string;
  } | null;
};

export type LocalPosePairViewerResult = {
  ok: boolean;
  project_dir?: string;
  run_id: string;
  run_mode: "local_only";
  integrity?: {
    status: "verified" | "legacy_unverified" | string;
    source?: string;
    receptor_sha256?: string;
    input_sha256?: string;
    optimized_sha256?: string;
    flex_input_sha256?: string;
  };
  coordinate_frame?: {
    source?: string;
    same_receptor_frame: boolean;
    alignment_applied: boolean;
    flexible_receptor?: boolean;
  };
  receptor?: ViewerStructureResult | null;
  flex_receptor_input?: ViewerStructureResult | null;
  flex_receptor_optimized?: ViewerStructureResult | null;
  input?: ViewerStructureResult | null;
  optimized?: ViewerStructureResult | null;
  comparison?: VinaEvaluationGeometry | null;
  message: string;
  warnings: string[];
  error?: {
    code: string;
    message: string;
    raw_error?: string;
    suggestion?: string;
  } | null;
};

export type ScreeningPoseViewerResult = {
  ok: boolean;
  project_dir?: string;
  screening_id?: string;
  item_id: string;
  source_file?: string;
  best_affinity_kcal_mol?: number | null;
  available_modes?: number[];
  mode?: number;
  receptor?: ViewerStructureResult | null;
  pose?: ViewerStructureResult | null;
  integrity?: {
    status: "verified" | "legacy_unverified" | string;
    receptor_sha256?: string;
    output_sha256?: string;
    expected_receptor_sha256?: string;
    expected_output_sha256?: string;
  };
  message: string;
  warnings: string[];
  error?: {
    code: string;
    message: string;
    raw_error?: string;
    suggestion?: string;
  } | null;
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
  joint_affinity_kcal_mol?: number;
  rmsd_lb: number;
  rmsd_ub: number;
  pose_available?: boolean;
  score_scope?: "joint_two_ligand_pose" | string;
};

export type VinaRunMode = "dock" | "score_only" | "local_only";

export type PoseInputAttestation = {
  version: 1;
  confirmed_at: string;
  receptor_sha256: string;
  ligand_sha256: string;
  flex_sha256?: string;
  claim: "same_receptor_coordinate_frame";
};

export type PoseInputAttestationStatus = {
  required: boolean;
  valid: boolean;
  status: "not_required" | "missing" | "invalid" | "stale" | "confirmed" | string;
  confirmed_at: string;
  receptor_sha256: string;
  ligand_sha256: string;
  flex_sha256: string;
  claim: string;
  current_receptor_sha256: string;
  current_ligand_sha256: string;
  current_flex_sha256: string;
  message: string;
  attestation: PoseInputAttestation | null;
  error: {
    code: string;
    message: string;
    raw_error?: string;
    suggestion?: string;
  } | null;
};

export type LocalPoseKind = "input" | "optimized";

export type LocalPoseView = LocalPoseKind | "overlay";

export type VinaEnergyTerm = {
  key: string;
  label: string;
  value_kcal_mol: number;
  term_number: number | null;
  line_number?: number;
};

export type VinaEvaluationGeometry = {
  ok?: boolean;
  heavy_atom_count?: number;
  heavy_atom_rmsd_no_alignment_angstrom?: number | null;
  heavy_atom_rmsd_aligned_angstrom?: number | null;
  mean_heavy_atom_displacement_angstrom?: number | null;
  max_heavy_atom_displacement_angstrom?: number | null;
  centroid_displacement_angstrom?: number | null;
  matched_by?: string;
  mapping_method?: string;
  error?: {
    code?: string;
    message?: string;
    raw_error?: string;
    suggestion?: string;
  } | null;
};

export type VinaEvaluationComparison = {
  comparable?: boolean;
  reason?: string | null;
  input_score_kcal_mol?: number | null;
  optimized_score_kcal_mol?: number | null;
  delta_score_kcal_mol?: number | null;
  delta_definition?: string;
  geometry?: VinaEvaluationGeometry;
};

export type VinaEvaluationStage = {
  id?: string;
  stage_id?: string;
  kind?: string;
  name?: string;
  label?: string;
  status?: string;
  score_kcal_mol?: number | null;
  primary_score_kcal_mol?: number | null;
  started_at?: string;
  finished_at?: string;
  elapsed_seconds?: number | null;
  duration_seconds?: number | null;
  command?: string[];
  log_file?: string;
  stdout_file?: string;
  stderr_file?: string;
  output_file?: string;
  output_pose_file?: string;
  exit_code?: number | null;
  error_message?: string;
  error?: {
    code?: string;
    message?: string;
  } | null;
};

export type VinaEvaluation = {
  schema_version: number;
  run_id: string;
  run_mode: Exclude<VinaRunMode, "dock">;
  kind: Exclude<VinaRunMode, "dock">;
  analyzed_at: string;
  scoring_protocol: "vina" | "ad4_maps";
  scoring_function: string;
  primary_score_kcal_mol: number;
  energy_terms: VinaEnergyTerm[];
  input_energy_terms?: VinaEnergyTerm[];
  comparison?: VinaEvaluationComparison;
  stages?: VinaEvaluationStage[];
  grid: {
    mode: "autobox" | "project_box" | string;
    center: string;
    size: string;
    spacing_angstrom: number | null;
  };
  autobox: boolean;
  input_pose_file: string;
  output_pose_file: string;
  pose_file: string;
  output_pose_generated: boolean;
  unbound_energy?: {
    mode: "explicit" | "vina_default";
    value_kcal_mol: number | null;
    comparison_warning?: string;
  };
  scientific_note: string;
};

export type DockStartSettings = {
  tool_paths: {
    vina: string;
    python: string;
    autogrid4: string;
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

export type VinaSettings = {
  scoring: "vina" | "vinardo";
  exhaustiveness: number;
  max_evals: number;
  num_modes: number;
  min_rmsd: number;
  energy_range: number;
  spacing: number;
  verbosity: 1 | 2;
  no_refine: boolean;
  force_even_voxels: boolean;
  unbound_energy: number | null;
  cpu: number;
  seed: number | null;
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
  vina: VinaSettings;
  config: {
    vina_config_file: string;
    generated_at: string;
  };
  preparation: PreparationState;
  runs: Array<Record<string, unknown>>;
  docking_protocol?: {
    mode?: "rigid" | "flexible";
    receptor_mode?: "rigid" | "flexible";
    engine?: "vina" | "ad4_maps";
    grid_source?: "receptor" | "precomputed_maps";
    map_set_id?: string;
    active_map_set_id?: string;
    protocol_id?: string;
    run_mode?: VinaRunMode;
    autobox?: boolean;
    pose_input_attestation?: PoseInputAttestation;
    [key: string]: unknown;
  };
};

export type AutoGridMapsDefaults = {
  spacing: number;
  grid_points: { x: number; y: number; z: number };
  center: { x: number; y: number; z: number };
  actual_size: { x: number; y: number; z: number };
  receptor_atom_types: string[];
  ligand_atom_types: string[];
  parameter_file: string;
};

export type AutoGridMapsManifest = {
  map_set_id: string;
  status: string;
  source: "generated" | "imported" | string;
  protocol_id?: string;
  parameter_file?: {
    relative_path?: string;
    sha256?: string;
  };
  grid?: {
    center?: { x: number; y: number; z: number };
    grid_points?: { x: number; y: number; z: number };
    spacing?: number;
    actual_size?: { x: number; y: number; z: number };
  };
  maps?: {
    prefix?: string;
    ligand_atom_types?: string[];
    files?: Array<{ name?: string; relative_path?: string; sha256?: string; size_bytes?: number }>;
  };
  autogrid?: {
    path?: string;
    version?: string;
    source?: string;
    log_file?: string;
    exit_code?: number | null;
  };
};

export type AutoGridMapsDefaultsResponse = {
  ok: boolean;
  project_dir?: string;
  project: DockStartProject | null;
  protocol?: "vina" | "ad4_maps" | "ad4zn_beta";
  protocol_id?: string;
  ad4zn?: Ad4ZnStatusResponse | null;
  defaults?: AutoGridMapsDefaults;
  message?: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type AutoGridMapsStatusResponse = {
  ok: boolean;
  ready: boolean;
  project_dir?: string;
  project: DockStartProject | null;
  protocol?: "vina" | "ad4_maps" | "ad4zn_beta";
  protocol_id?: string;
  protocol_active?: boolean;
  map_set_id?: string;
  manifest_file?: string;
  manifest?: AutoGridMapsManifest | null;
  maps_prefix?: string;
  ligand_atom_types?: string[];
  issues?: string[];
  tool?: ToolCheckResult;
  message?: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type Ad4ZnConfirmationKey =
  | "target"
  | "coordination"
  | "protonation"
  | "water"
  | "cofactor"
  | "tz_pseudoatom"
  | "zinc_only_scope";

export type Ad4ZnCoordinate = {
  x: number;
  y: number;
  z: number;
};

export type Ad4ZnAtom = {
  atom_id: string;
  record_type: "ATOM" | "HETATM" | string;
  serial: number;
  name: string;
  altloc: string;
  residue_name: string;
  chain: string;
  residue_number: string;
  insertion_code: string;
  atom_type: string;
  charge: number | null;
  coordinate: Ad4ZnCoordinate;
  line_number: number;
};

export type Ad4ZnNeighbor = {
  atom: Ad4ZnAtom;
  distance: number;
  eligible: boolean;
  coordinating: boolean;
  excluded_by_connectivity?: boolean;
};

export type Ad4ZnCoordinationGroup = {
  group_id: string;
  kind: "atom" | "carboxylate";
  representative_coordinate: Ad4ZnCoordinate;
  representative_distance: number;
  members: Ad4ZnAtom[];
};

export type Ad4ZnSite = {
  site_id: string;
  zn: Ad4ZnAtom;
  neighbors: Ad4ZnNeighbor[];
  coordination_groups: Ad4ZnCoordinationGroup[];
  coordination_number: number;
  can_generate: boolean;
  tz_candidate: {
    coordinate: Ad4ZnCoordinate;
    distance: number;
    charge: number;
    direction: Ad4ZnCoordinate;
  } | null;
  geometry_message: string;
  nearby_metals?: Array<{
    atom: Ad4ZnAtom;
    distance: number;
  }>;
  plane_separation_degrees?: number | null;
  plane_separation_angstrom?: number | null;
  limits: {
    neighbor_search_angstrom: number;
    coordination_cutoff_angstrom: number;
    tz_distance_angstrom: number;
    minimum_plane_separation_degrees?: number;
  };
};

export type Ad4ZnFileRecord = {
  relative_path?: string;
  sha256?: string;
  size_bytes?: number;
  source_sha256?: string;
  receptor_sha256?: string;
  selected_site_id?: string;
  created_at?: string;
  generated_at?: string;
  [key: string]: unknown;
};

export type Ad4ZnReview = {
  recorded: boolean;
  valid: boolean;
  protocol_id?: "ad4zn_beta";
  schema_version?: number;
  selected_site_id?: string;
  confirmations?: Partial<Record<Ad4ZnConfirmationKey, boolean>>;
  receptor_relative_path?: string;
  receptor_sha256?: string;
  review_binding_sha256?: string;
  saved_at?: string;
  invalid_reason: string;
  [key: string]: unknown;
};

export type Ad4ZnPreparedReceptor = Ad4ZnFileRecord & {
  recorded: boolean;
  valid: boolean;
  protocol_id?: "ad4zn_beta";
  schema_version?: number;
  algorithm_name?: string;
  input_relative_path?: string;
  input_sha256?: string;
  selected_site_id?: string;
  algorithm_version?: string;
  all_zn_charge_changes?: Array<Record<string, unknown>>;
  tz?: Record<string, unknown>;
  removed_existing_tz_count?: number;
  prepared_at?: string;
  invalid_reason: string;
};

export type Ad4ZnParameterFile = Ad4ZnFileRecord & {
  recorded: boolean;
  valid: boolean;
  protocol_id?: "ad4zn_beta";
  schema_version?: number;
  source_path?: string;
  size_bytes?: number;
  license_source?: string;
  license_id?: string;
  license_notice_detected?: boolean;
  supported_profile_id?: string;
  upstream_reference?: string;
  reference_sha256?: string;
  matches_reference_sha256?: boolean;
  coefficients?: Record<string, unknown>;
  atom_types?: string[];
  recorded_at?: string;
  invalid_reason: string;
};

export type Ad4ZnIssue = {
  code: string;
  title: string;
  message: string;
  raw_error: string;
  suggestion: string;
  blocking: boolean;
};

export type Ad4ZnStepReadiness = {
  review: boolean;
  prepare: boolean;
  parameter: boolean;
  run: boolean;
};

export type Ad4ZnStatusResponse = {
  ok: boolean;
  project_dir?: string;
  project?: DockStartProject | null;
  protocol_id: "ad4zn_beta";
  state_schema_version: number;
  algorithm_version: string;
  algorithm_reference?: string;
  algorithm_reference_sha256?: string;
  preparation_ready: boolean;
  ready: boolean;
  receptor: Ad4ZnFileRecord | null;
  sites: Ad4ZnSite[];
  other_metals: Ad4ZnAtom[];
  existing_tz: Ad4ZnAtom[];
  selected_site: Ad4ZnSite | null;
  selected_site_id?: string;
  review: Ad4ZnReview;
  review_valid: boolean;
  prepared_receptor: Ad4ZnPreparedReceptor;
  prepared_receptor_valid: boolean;
  parameter_file: Ad4ZnParameterFile;
  parameter_file_valid: boolean;
  required_confirmations?: Ad4ZnConfirmationKey[];
  step_readiness: Ad4ZnStepReadiness;
  compatibility: {
    autogrid_min_version: string;
    tool_checked: boolean;
    maps_pipeline_connected: boolean;
  };
  issues: Ad4ZnIssue[];
  message?: string;
  error?: {
    code: string;
    title?: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};

export type VinaMapsGridSource = "receptor" | "precomputed_maps";

export type VinaMapsCurrentContext = {
  scoring_function?: "vina" | "vinardo" | string;
  receptor?: {
    file?: string;
    source_sha256?: string;
  };
  grid?: {
    requested_box?: {
      center?: { x?: number; y?: number; z?: number };
      size?: { x?: number; y?: number; z?: number };
    };
    spacing?: number;
    force_even_voxels?: boolean;
  };
  vina?: {
    version?: string;
    path?: string;
    source?: string;
    sha256?: string;
    size_bytes?: number;
  };
  raw_import_attestation_template?: {
    version?: number;
    confirmed?: boolean;
    scoring_function?: "vina" | "vinardo" | string;
    receptor_sha256?: string;
    vina_binary_sha256?: string;
    statement?: string;
  };
};

export type VinaMapsManifest = {
  schema_version?: number;
  map_set_id?: string;
  status?: string;
  protocol_id?: string;
  source?: "generated" | "imported" | string;
  created_at?: string;
  scoring_function?: "vina" | "vinardo" | string;
  receptor?: {
    file?: string;
    source_file?: string;
    source_sha256?: string;
  };
  grid?: {
    requested_box?: {
      center?: { x?: number; y?: number; z?: number };
      size?: { x?: number; y?: number; z?: number };
    };
    center?: { x?: number; y?: number; z?: number };
    spacing?: number;
    nelements?: { x?: number; y?: number; z?: number };
    actual_size?: { x?: number; y?: number; z?: number };
    force_even_voxels?: boolean;
  };
  vina?: {
    version?: string;
    path?: string;
    source?: string;
    sha256?: string;
    size_bytes?: number;
    command?: string[];
  };
  maps?: {
    prefix?: string;
    atom_types?: string[];
    files?: Array<{
      name?: string;
      relative_path?: string;
      sha256?: string;
      size_bytes?: number;
    }>;
    map_count?: number;
    total_size_bytes?: number;
    payload_sha256?: string;
  };
  semantics?: {
    grid_only?: boolean;
    no_refine_equivalent?: boolean;
    rigid_receptor_only?: boolean;
  };
};

export type VinaMapsStatusResponse = {
  ok: boolean;
  ready: boolean;
  project?: DockStartProject | null;
  project_dir?: string;
  protocol_active?: boolean;
  grid_source?: VinaMapsGridSource;
  map_set_id?: string;
  manifest_file?: string;
  manifest?: VinaMapsManifest | null;
  maps_prefix?: string;
  issues?: string[];
  compatibility_probe?: Record<string, unknown> | null;
  current_context?: VinaMapsCurrentContext | null;
  tool?: ToolCheckResult;
  message?: string;
  error?: {
    code?: string;
    title?: string;
    message?: string;
    raw_error?: string;
    suggestion?: string;
  } | null;
};

export type PreparationStatusResponse = {
  ok: boolean;
  project_dir: string;
  project: DockStartProject | null;
  preparation: PreparationState | null;
  structure_review?: StructureReviewPayload;
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
  evaluation?: VinaEvaluation;
  evaluation_file?: string;
  scores_file?: string;
  project_scores_file?: string;
  best_affinity?: number | null;
  primary_score_kcal_mol?: number | null;
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
  analysis_status?: RunFileStatus | null;
  can_export?: boolean;
  demo_type?: string;
  entry_step?: string;
  entry_page?: string;
  entry_run_id?: string;
  target_name?: string;
  disclaimer?: string;
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

export type WorkflowFileStatus = {
  path: string;
  absolute_path?: string;
  exists: boolean;
  non_empty: boolean;
  size: number;
  status: "ok" | "missing" | "empty" | "error" | string;
};

export type WorkflowRunSummary = {
  run_id?: string;
  status?: string;
  stage?: string;
  run_mode?: VinaRunMode;
  autobox?: boolean;
  output_file?: string;
  pose_file?: string;
  [key: string]: unknown;
};

export type ProjectWorkflowStatusResponse = {
  ok: boolean;
  project_dir: string;
  project: DockStartProject | null;
  raw?: {
    receptor?: WorkflowFileStatus;
    ligand?: WorkflowFileStatus;
  };
  prepared?: {
    receptor?: WorkflowFileStatus;
    ligand?: WorkflowFileStatus;
  };
  preparation?: {
    receptor?: Record<string, unknown>;
    ligand?: Record<string, unknown>;
  };
  box?: {
    status: "ok" | "error" | string;
    warnings?: string[];
    error?: {
      code?: string;
      message?: string;
      raw_error?: string;
      suggestion?: string;
    } | null;
  };
  vina?: {
    status: "ok" | "error" | string;
    warnings?: string[];
    error?: {
      code?: string;
      message?: string;
      raw_error?: string;
      suggestion?: string;
    } | null;
  };
  config?: WorkflowFileStatus;
  latest_run?: WorkflowRunSummary | null;
  latest_run_for_current_mode?: WorkflowRunSummary | null;
  viewer?: {
    can_view_raw_receptor: boolean;
    can_view_raw_ligand: boolean;
    can_view_prepared_receptor: boolean;
    can_view_prepared_ligand: boolean;
    can_view_docking_output: boolean;
    available_runs: Array<Record<string, unknown>>;
    recommended_viewer_action: string;
  };
  next_recommended_action?: string;
  message?: string;
  error?: {
    code: string;
    message: string;
    raw_error: string;
    suggestion: string;
  } | null;
};
