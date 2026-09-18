#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    collections::{hash_map::DefaultHasher, HashMap, HashSet, VecDeque},
    env, fs,
    hash::{Hash, Hasher},
    io::{Read, Seek, SeekFrom},
    path::{Component, Path, PathBuf},
    process::{Child, Command, ExitStatus, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Condvar, Mutex, OnceLock,
    },
    time::{Duration, Instant},
};

use serde::Serialize;
#[cfg(windows)]
use std::os::windows::process::CommandExt;

use tauri::Emitter;
#[cfg(not(debug_assertions))]
use tauri::Manager;

const RESOURCE_DIR_ENV_VAR: &str = "DOCKSTART_RESOURCE_DIR";
const DEV_BACKEND_DIR_ENV_VAR: &str = "DOCKSTART_DEV_BACKEND_DIR";
const ALLOW_DEV_BACKEND_DISCOVERY_ENV_VAR: &str = "DOCKSTART_ALLOW_DEV_BACKEND_DISCOVERY";
const SETTINGS_ENV_VAR: &str = "DOCKSTART_SETTINGS_PATH";
const PREPARATION_TOOLS_SNAPSHOT_ENV_VAR: &str = "DOCKSTART_PREPARATION_TOOLS_JSON";
const RUNTIME_CACHE_TTL: Duration = Duration::from_secs(10 * 60);
const VIEWER_CACHE_TTL: Duration = Duration::from_secs(15 * 60);
const MAX_BACKEND_CACHE_AGE: Duration = Duration::from_secs(30 * 60);
const MAX_BACKEND_CACHE_ENTRIES: usize = 128;
const MAX_BACKEND_CACHE_BYTES: usize = 64 * 1024 * 1024;
const MAX_BACKEND_CACHE_ENTRY_BYTES: usize = 20 * 1024 * 1024;
const INTERACTIVE_STRUCTURE_PREVIEW_BYTES: usize = 2 * 1024 * 1024;
const MAX_MARKDOWN_PREVIEW_BYTES: u64 = 2 * 1024 * 1024;
const BACKGROUND_TASK_EVENT: &str = "dockstart-background-task";
const MAX_CONCURRENT_BACKGROUND_TASKS: usize = 2;
const MAX_QUEUED_BACKGROUND_TASKS: usize = 32;
const MANAGED_PROCESS_POLL_INTERVAL: Duration = Duration::from_millis(100);

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

#[cfg(windows)]
struct ProcessTreeGuard {
    job: *mut std::ffi::c_void,
}

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn CreateJobObjectW(
        job_attributes: *const std::ffi::c_void,
        name: *const u16,
    ) -> *mut std::ffi::c_void;
    fn AssignProcessToJobObject(job: *mut std::ffi::c_void, process: *mut std::ffi::c_void) -> i32;
    fn TerminateJobObject(job: *mut std::ffi::c_void, exit_code: u32) -> i32;
    fn CloseHandle(handle: *mut std::ffi::c_void) -> i32;
    fn GetLastError() -> u32;
}

#[cfg(windows)]
impl ProcessTreeGuard {
    fn attach(child: &Child) -> Result<Self, String> {
        use std::os::windows::io::AsRawHandle;

        // SAFETY: null security/name pointers request an unnamed job with
        // default security. The returned handle is owned by this guard.
        let job = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if job.is_null() {
            // SAFETY: GetLastError has no preconditions.
            return Err(format!(
                "无法创建 Windows 进程作业，错误码 {}。",
                unsafe { GetLastError() }
            ));
        }
        // SAFETY: both handles are valid here; the child remains alive while
        // it is attached and the job handle is closed by Drop.
        if unsafe { AssignProcessToJobObject(job, child.as_raw_handle()) } == 0 {
            // SAFETY: `job` was created successfully above and is closed once.
            let error = unsafe { GetLastError() };
            unsafe { CloseHandle(job) };
            return Err(format!(
                "无法把后台工具加入 Windows 进程作业，错误码 {error}。"
            ));
        }
        Ok(Self { job })
    }

    fn terminate(&self) {
        // SAFETY: the guard owns a live job handle until Drop. Terminating an
        // already-finished job is harmless and ensures descendants do not
        // outlive the Python wrapper.
        unsafe {
            TerminateJobObject(self.job, 1);
        }
    }
}

#[cfg(windows)]
impl Drop for ProcessTreeGuard {
    fn drop(&mut self) {
        // SAFETY: this is the unique owned handle returned by CreateJobObjectW.
        unsafe {
            CloseHandle(self.job);
        }
    }
}

#[cfg(not(windows))]
struct ProcessTreeGuard;

#[cfg(not(windows))]
impl ProcessTreeGuard {
    fn attach(_child: &Child) -> Result<Self, String> {
        Ok(Self)
    }

    fn terminate(&self) {}
}

fn distribution_manifest_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();

    if let Some(resource_dir) = env::var_os(RESOURCE_DIR_ENV_VAR)
        .map(PathBuf::from)
        .and_then(|path| fs::canonicalize(path).ok())
    {
        candidates.push(
            resource_dir
                .join("resources")
                .join("toolchain_manifest.json"),
        );
        candidates.push(resource_dir.join("toolchain_manifest.json"));
    }

    if let Some(backend_dir) = find_backend_dir() {
        if let Some(repo_root) = backend_dir.parent() {
            candidates.push(repo_root.join("resources").join("toolchain_manifest.json"));
        }
    }

    if development_backend_discovery_enabled() {
        if let Ok(current_dir) = env::current_dir() {
            for ancestor in current_dir.ancestors().take(6) {
                candidates.push(ancestor.join("resources").join("toolchain_manifest.json"));
            }
        }
    }

    let mut seen = HashSet::new();
    candidates.retain(|candidate| seen.insert(candidate.clone()));
    candidates
}

fn distribution_profile_from_manifest(manifest_path: &Path) -> Result<serde_json::Value, String> {
    let content =
        fs::read_to_string(manifest_path).map_err(|error| format!("无法读取发布清单：{error}"))?;
    let manifest: serde_json::Value = serde_json::from_str(&content)
        .map_err(|error| format!("发布清单不是有效 JSON：{error}"))?;
    let release_profile = manifest
        .get("release_profile")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("");
    let display_name = match release_profile {
        "basic_stable" => "Basic",
        "assisted_stable" => "Assisted",
        _ => {
            return Err(format!(
                "发布清单中的 release_profile 无法识别：{}",
                if release_profile.is_empty() {
                    "<empty>"
                } else {
                    release_profile
                }
            ))
        }
    };

    Ok(serde_json::json!({
        "ok": true,
        "release_profile": release_profile,
        "display_name": display_name,
        "manifest_file": manifest_path.to_string_lossy(),
        "message": format!("当前安装为 DockStart {display_name} · 本地候选。"),
        "error": serde_json::Value::Null,
    }))
}

#[tauri::command]
fn get_distribution_profile() -> String {
    let mut errors = Vec::new();
    for manifest_path in distribution_manifest_candidates() {
        if !manifest_path.is_file() {
            continue;
        }
        match distribution_profile_from_manifest(&manifest_path) {
            Ok(profile) => {
                return serde_json::to_string(&profile).unwrap_or_else(|error| {
                    serde_json::json!({
                        "ok": false,
                        "release_profile": "unknown",
                        "display_name": "Profile 未知",
                        "manifest_file": manifest_path.to_string_lossy(),
                        "message": "无法显示当前安装版本。",
                        "error": error.to_string(),
                    })
                    .to_string()
                })
            }
            Err(error) => errors.push(format!("{}: {error}", manifest_path.display())),
        }
    }

    serde_json::json!({
        "ok": false,
        "release_profile": "unknown",
        "display_name": "Profile 未知",
        "manifest_file": "",
        "message": "没有找到可识别的 DockStart 发布清单。",
        "error": errors.join("\n"),
    })
    .to_string()
}

#[tauri::command]
fn refresh_runtime_cache() -> String {
    invalidate_backend_cache(CacheInvalidation::Runtime);
    format!(
        "{{\"ok\":true,\"runtime_fingerprint\":\"{}\",\"message\":\"运行时检测缓存已清除，下一次检测将重新核验工具链。\"}}",
        json_escape(&runtime_fingerprint())
    )
}

#[tauri::command]
async fn check_tools() -> String {
    match run_backend_module_cached_async(
        "dockstart_core.tool_check",
        Vec::new(),
        RUNTIME_CACHE_TTL,
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_check_error_json("无法读取本机工具状态。", &error),
    }
}

#[tauri::command]
async fn get_toolchain_status() -> String {
    match run_backend_module_cached_async("dockstart_core.toolchain", Vec::new(), RUNTIME_CACHE_TTL)
        .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_toolchain_error_json("无法读取 DockStart 内置工具链状态。", &error),
    }
}

#[tauri::command]
async fn get_toolchain_repair_suggestions() -> String {
    match run_backend_module_cached_async(
        "dockstart_core.toolchain_repair",
        Vec::new(),
        RUNTIME_CACHE_TTL,
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法生成工具链修复建议。", &error),
    }
}

#[tauri::command]
async fn run_post_install_check() -> String {
    match run_backend_module_async("dockstart_core.diagnostics", vec!["check".to_string()]).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法运行安装后自检。", &error),
    }
}

#[tauri::command]
async fn export_diagnostic_report(output_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.diagnostics",
        vec!["export".to_string(), output_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导出诊断报告。", &error),
    }
}

#[tauri::command]
async fn get_app_capability_profile() -> String {
    match run_backend_module_cached_async(
        "dockstart_core.capabilities",
        vec!["profile".to_string()],
        Duration::from_secs(1),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 DockStart 运行模式能力。", &error),
    }
}

#[tauri::command]
async fn get_project_mode_recommendation(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.capabilities",
        vec!["project-recommendation".to_string(), project_dir],
        Duration::from_millis(250),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法生成项目运行模式建议。", &error),
    }
}

#[tauri::command]
async fn get_minimum_requirements_status(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.capabilities",
        vec!["minimum-requirements".to_string(), project_dir],
        Duration::from_millis(250),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取项目最低依赖状态。", &error),
    }
}

#[tauri::command]
async fn list_available_demo_projects() -> String {
    match run_backend_module_cached_async(
        "dockstart_core.demo_projects",
        vec!["list".to_string()],
        Duration::from_secs(1),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取示例项目列表。", &error),
    }
}

#[tauri::command]
async fn create_demo_project(destination_dir: String, demo_type: String) -> String {
    match run_backend_module_async(
        "dockstart_core.demo_projects",
        vec!["create".to_string(), destination_dir, demo_type],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法创建示例项目。", &error),
    }
}

#[tauri::command]
async fn validate_demo_project(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.demo_projects",
        vec!["validate".to_string(), project_dir],
        Duration::from_millis(250),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法校验示例项目。", &error),
    }
}

#[tauri::command]
async fn get_settings() -> String {
    match run_backend_module_cached_async(
        "dockstart_core.settings",
        vec!["get".to_string()],
        Duration::from_millis(250),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_settings_error_json("无法读取 DockStart 设置。", &error),
    }
}

#[tauri::command]
async fn save_settings(settings_json: String) -> String {
    match run_backend_module_async(
        "dockstart_core.settings",
        vec!["save-json".to_string(), settings_json],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_settings_error_json("无法保存 DockStart 设置。", &error),
    }
}

#[tauri::command]
async fn update_tool_path(tool_key: String, path: String) -> String {
    match run_backend_module_async(
        "dockstart_core.settings",
        vec!["update-tool-path".to_string(), tool_key, path],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_settings_error_json("无法更新工具路径。", &error),
    }
}

#[tauri::command]
async fn diagnose_settings() -> String {
    match run_backend_module_async(
        "dockstart_core.settings",
        vec!["diagnose".to_string()],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_settings_error_json("无法诊断 DockStart 设置存储。", &error),
    }
}

#[tauri::command]
async fn create_project(project_name: String, base_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.project",
        vec!["create".to_string(), project_name, base_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法创建 DockStart 项目。", &error),
    }
}

#[tauri::command]
async fn load_project(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.project",
        vec!["load".to_string(), project_dir],
        Duration::from_millis(150),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 DockStart 项目。", &error),
    }
}

#[tauri::command]
async fn import_receptor_pdbqt(
    project_dir: String,
    source_path: String,
    source_label: Option<String>,
) -> String {
    let mut args = vec!["import-receptor".to_string(), project_dir, source_path];
    if let Some(label) = source_label
        .as_deref()
        .map(normalize_pdbqt_source_label_arg)
        .filter(|value| !value.is_empty())
    {
        args.push(label);
    }
    match run_backend_module_async("dockstart_core.project", args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导入受体 PDBQT。", &error),
    }
}

#[tauri::command]
async fn import_ligand_pdbqt(
    project_dir: String,
    source_path: String,
    source_label: Option<String>,
) -> String {
    let mut args = vec!["import-ligand".to_string(), project_dir, source_path];
    if let Some(label) = source_label
        .as_deref()
        .map(normalize_pdbqt_source_label_arg)
        .filter(|value| !value.is_empty())
    {
        args.push(label);
    }
    match run_backend_module_async("dockstart_core.project", args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导入配体 PDBQT。", &error),
    }
}

fn normalize_pdbqt_source_label_arg(value: &str) -> String {
    let sanitized = value
        .chars()
        .filter_map(|character| {
            if character.is_control() {
                character.is_whitespace().then_some(' ')
            } else {
                Some(character)
            }
        })
        .collect::<String>();
    sanitized
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(240)
        .collect::<String>()
        .trim_end()
        .to_string()
}

#[tauri::command]
async fn search_rcsb_candidates(query: String, limit: u32, query_type: String) -> String {
    match run_backend_module_async(
        "dockstart_core.structure_fetch",
        vec![
            "search-rcsb".to_string(),
            query,
            limit.to_string(),
            query_type,
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法搜索 RCSB PDB 候选结构。", &error),
    }
}

#[tauri::command]
async fn search_pubchem_candidates(query: String, limit: u32, query_type: String) -> String {
    match run_backend_module_async(
        "dockstart_core.structure_fetch",
        vec![
            "search-pubchem".to_string(),
            query,
            limit.to_string(),
            query_type,
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法搜索 PubChem 候选化合物。", &error),
    }
}

#[tauri::command]
async fn preview_structure_candidate(selection_json: String) -> String {
    match run_backend_module_async(
        "dockstart_core.candidate_preview",
        vec![
            "preview-candidate".to_string(),
            selection_json,
            INTERACTIVE_STRUCTURE_PREVIEW_BYTES.to_string(),
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法加载候选结构的临时 3D 预览。", &error),
    }
}

#[tauri::command]
async fn fetch_pdb_structure(
    project_dir: String,
    pdb_id: String,
    format: String,
    overwrite: bool,
) -> String {
    match run_backend_module_async(
        "dockstart_core.structure_fetch",
        vec![
            "fetch-pdb".to_string(),
            project_dir,
            pdb_id,
            format,
            overwrite.to_string(),
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法下载 RCSB PDB 原始结构文件。", &error),
    }
}

#[tauri::command]
async fn fetch_pubchem_ligand(
    project_dir: String,
    query: String,
    query_type: String,
    format: String,
    overwrite: bool,
) -> String {
    match run_backend_module_async(
        "dockstart_core.structure_fetch",
        vec![
            "fetch-pubchem".to_string(),
            project_dir,
            query,
            format,
            overwrite.to_string(),
            query_type,
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法下载 PubChem 原始配体文件。", &error),
    }
}

#[tauri::command]
async fn get_raw_files_status(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.structure_fetch",
        vec!["raw-files-status".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 raw 文件状态。", &error),
    }
}

#[tauri::command]
async fn import_receptor_raw_file(project_dir: String, source_path: String) -> String {
    match run_backend_module_async(
        "dockstart_core.structure_fetch",
        vec!["import-receptor-raw".to_string(), project_dir, source_path],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导入受体原始结构文件。", &error),
    }
}

#[tauri::command]
async fn import_ligand_raw_file(project_dir: String, source_path: String) -> String {
    match run_backend_module_async(
        "dockstart_core.structure_fetch",
        vec!["import-ligand-raw".to_string(), project_dir, source_path],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导入配体原始结构文件。", &error),
    }
}

#[tauri::command]
async fn clear_receptor_raw_record(project_dir: String, delete_file: bool) -> String {
    match run_backend_module_async(
        "dockstart_core.structure_fetch",
        vec![
            "clear-receptor-raw".to_string(),
            project_dir,
            delete_file.to_string(),
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法清除受体 raw 记录。", &error),
    }
}

#[tauri::command]
async fn clear_ligand_raw_record(project_dir: String, delete_file: bool) -> String {
    match run_backend_module_async(
        "dockstart_core.structure_fetch",
        vec![
            "clear-ligand-raw".to_string(),
            project_dir,
            delete_file.to_string(),
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法清除配体 raw 记录。", &error),
    }
}

#[tauri::command]
async fn get_preparation_status(project_dir: String) -> String {
    let task =
        tauri::async_runtime::spawn_blocking(move || preparation_status_snapshot(&project_dir));
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => fallback_project_error_json("无法读取 PDBQT 自动准备状态。", &error),
        Err(error) => {
            fallback_project_error_json("PDBQT 自动准备状态任务异常结束。", &error.to_string())
        }
    }
}

#[tauri::command]
async fn get_structure_review(project_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.preparation",
        vec!["structure-review".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取结构文件信息。", &error),
    }
}

#[tauri::command]
async fn validate_preparation_prerequisites(project_dir: String, target: String) -> String {
    match run_backend_module_async(
        "dockstart_core.preparation",
        vec!["validate".to_string(), project_dir, target],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法完成 PDBQT 自动准备前置检查。", &error),
    }
}

#[tauri::command]
async fn get_preparation_tool_status(project_dir: String) -> String {
    let task = tauri::async_runtime::spawn_blocking(move || {
        preparation_tool_status_snapshot(&project_dir)
    });
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => fallback_project_error_json("无法读取 PDBQT 自动准备工具能力。", &error),
        Err(error) => {
            fallback_project_error_json("PDBQT 自动准备工具检测任务异常结束。", &error.to_string())
        }
    }
}

#[tauri::command]
async fn get_flexible_receptor_status(project_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.flexible_receptor",
        vec!["status".to_string(), "--project".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取柔性侧链受体状态。", &error),
    }
}

#[tauri::command]
async fn get_flexible_receptor_identity_context(project_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.flexible_receptor",
        vec!["identity".to_string(), "--project".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法建立柔性残基身份审查上下文。", &error),
    }
}

fn append_flexible_identity_args(
    args: &mut Vec<String>,
    resolved_altlocs: Option<HashMap<String, String>>,
    selection_context_sha256: Option<String>,
) -> Result<(), String> {
    let mut choices = resolved_altlocs
        .unwrap_or_default()
        .into_iter()
        .collect::<Vec<_>>();
    choices.sort_by(|left, right| left.0.cmp(&right.0));
    for (selector, altloc) in choices {
        let selector = selector.trim();
        let altloc = altloc.trim();
        if selector.is_empty()
            || altloc.len() != 1
            || !altloc.bytes().all(|value| value.is_ascii_alphanumeric())
        {
            return Err(format!("无效的柔性残基 altloc 选择：{selector}={altloc}"));
        }
        args.extend([
            "--resolved-altloc".to_string(),
            format!("{selector}={altloc}"),
        ]);
    }
    if let Some(context_sha256) = selection_context_sha256 {
        let context_sha256 = context_sha256.trim().to_ascii_lowercase();
        if !context_sha256.is_empty() {
            if !is_sha256_hex(&context_sha256) {
                return Err("柔性残基选择上下文 SHA256 无效。".to_string());
            }
            args.extend([
                "--expected-selection-context-sha256".to_string(),
                context_sha256,
            ]);
        }
    }
    Ok(())
}

#[tauri::command]
async fn validate_flexible_receptor(
    project_dir: String,
    residues: Vec<String>,
    max_residues: u8,
    resolved_altlocs: Option<HashMap<String, String>>,
    selection_context_sha256: Option<String>,
) -> String {
    let mut args = vec![
        "validate".to_string(),
        "--project".to_string(),
        project_dir,
        "--max-residues".to_string(),
        max_residues.to_string(),
    ];
    for residue in residues {
        args.extend(["--residue".to_string(), residue]);
    }
    if let Err(error) =
        append_flexible_identity_args(&mut args, resolved_altlocs, selection_context_sha256)
    {
        return fallback_project_error_json("柔性残基身份参数无效。", &error);
    }
    match run_backend_module_async("dockstart_core.flexible_receptor", args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("柔性残基检查失败。", &error),
    }
}

#[tauri::command]
async fn set_receptor_docking_mode(project_dir: String, mode: String) -> String {
    match run_backend_module_async(
        "dockstart_core.flexible_receptor",
        vec![
            "set-mode".to_string(),
            "--project".to_string(),
            project_dir,
            "--mode".to_string(),
            mode,
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法切换受体刚性/柔性模式。", &error),
    }
}

const MACROCYCLE_BACKEND_MODULE: &str = "dockstart_core.macrocycle";

fn macrocycle_status_args(project_dir: String) -> Vec<String> {
    vec!["status".to_string(), "--project".to_string(), project_dir]
}

fn macrocycle_review_args(project_dir: String, options_json: Option<String>) -> Vec<String> {
    let mut args = vec!["review".to_string(), "--project".to_string(), project_dir];
    if let Some(options) = options_json.filter(|value| !value.trim().is_empty()) {
        args.extend(["--options-json".to_string(), options]);
    }
    args
}

fn macrocycle_confirm_args(
    project_dir: String,
    review_id: String,
    candidate_id: Option<String>,
    rigid: bool,
) -> Result<Vec<String>, String> {
    let candidate_id = candidate_id.filter(|value| !value.trim().is_empty());
    let mut args = vec![
        "confirm".to_string(),
        "--project".to_string(),
        project_dir,
        "--review-id".to_string(),
        review_id,
    ];
    match (candidate_id, rigid) {
        (Some(candidate_id), false) => {
            args.extend(["--candidate-id".to_string(), candidate_id]);
        }
        (None, true) => {
            args.push("--rigid".to_string());
        }
        (Some(_), true) => {
            return Err("候选断环与刚性模式不能同时确认。".to_string());
        }
        (None, false) => {
            return Err("必须选择一个候选断环，或明确确认刚性模式。".to_string());
        }
    }
    Ok(args)
}

fn macrocycle_reset_args(project_dir: String) -> Vec<String> {
    vec!["reset".to_string(), "--project".to_string(), project_dir]
}

#[tauri::command]
async fn get_macrocycle_status(project_dir: String) -> String {
    match run_backend_json_module_async(
        MACROCYCLE_BACKEND_MODULE,
        macrocycle_status_args(project_dir),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取大环配体审查状态。", &error),
    }
}

#[tauri::command]
async fn review_ligand_macrocycle(project_dir: String, options_json: Option<String>) -> String {
    let args = macrocycle_review_args(project_dir.clone(), options_json);
    match run_preparation_json_module_async(project_dir, MACROCYCLE_BACKEND_MODULE, args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法分析大环配体断环候选。", &error),
    }
}

#[tauri::command]
async fn confirm_ligand_macrocycle(
    project_dir: String,
    review_id: String,
    candidate_id: Option<String>,
    rigid: bool,
) -> String {
    let args = match macrocycle_confirm_args(project_dir, review_id, candidate_id, rigid) {
        Ok(args) => args,
        Err(error) => return fallback_project_error_json("无法确认大环配体处理方式。", &error),
    };
    match run_backend_json_module_async(MACROCYCLE_BACKEND_MODULE, args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法确认大环配体处理方式。", &error),
    }
}

#[tauri::command]
async fn reset_ligand_macrocycle_confirmation(project_dir: String) -> String {
    match run_backend_json_module_async(
        MACROCYCLE_BACKEND_MODULE,
        macrocycle_reset_args(project_dir),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法重置大环配体确认记录。", &error),
    }
}

#[tauri::command]
async fn get_result_export_status(project_dir: String, run_id: String) -> String {
    match run_backend_module_async(
        "dockstart_core.result_export",
        vec![
            "status".to_string(),
            "--project".to_string(),
            project_dir,
            "--run".to_string(),
            run_id,
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 Meeko SDF 导出状态。", &error),
    }
}

#[tauri::command]
async fn prepare_ligand_pdbqt(
    project_dir: String,
    overwrite: bool,
    options_json: Option<String>,
) -> String {
    let mut args = vec![
        "prepare-ligand".to_string(),
        project_dir,
        overwrite.to_string(),
    ];
    if let Some(options) = options_json.filter(|value| !value.trim().is_empty()) {
        args.push(options);
    }
    match run_backend_module_async("dockstart_core.preparation", args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法自动准备 ligand PDBQT。", &error),
    }
}

#[tauri::command]
async fn load_ligand_preparation_log(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.preparation",
        vec!["ligand-log".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 ligand preparation 日志。", &error),
    }
}

#[tauri::command]
async fn prepare_receptor_pdbqt(
    project_dir: String,
    overwrite: bool,
    options_json: Option<String>,
) -> String {
    let mut args = vec![
        "prepare-receptor".to_string(),
        project_dir,
        overwrite.to_string(),
    ];
    if let Some(options) = options_json.filter(|value| !value.trim().is_empty()) {
        args.push(options);
    }
    match run_backend_module_async("dockstart_core.preparation", args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法自动准备 receptor PDBQT。", &error),
    }
}

#[tauri::command]
async fn load_receptor_preparation_log(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.preparation",
        vec!["receptor-log".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 receptor preparation 日志。", &error),
    }
}

#[tauri::command]
async fn list_preparation_runs(project_dir: String, target: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.preparation",
        vec!["list-runs".to_string(), project_dir, target],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法列出 preparation 记录。", &error),
    }
}

#[tauri::command]
async fn load_preparation_metadata(project_dir: String, target: String, prep_id: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.preparation",
        vec!["metadata".to_string(), project_dir, target, prep_id],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 preparation metadata。", &error),
    }
}

#[tauri::command]
async fn get_latest_preparation(project_dir: String, target: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.preparation",
        vec!["latest".to_string(), project_dir, target],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 latest preparation。", &error),
    }
}

#[tauri::command]
async fn reset_preparation_status(project_dir: String, target: String) -> String {
    match run_backend_module_async(
        "dockstart_core.preparation",
        vec!["reset".to_string(), project_dir, target],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法重置 PDBQT 自动准备状态。", &error),
    }
}

#[tauri::command]
async fn get_box_params(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.project",
        vec!["get-box".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 Box 参数。", &error),
    }
}

#[tauri::command]
async fn update_box_params(project_dir: String, box_json: String) -> String {
    match run_backend_module_async(
        "dockstart_core.project",
        vec!["update-box".to_string(), project_dir, box_json],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法保存 Box 参数。", &error),
    }
}

#[tauri::command]
async fn get_vina_params(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.project",
        vec!["get-vina".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 Vina 参数。", &error),
    }
}

#[tauri::command]
async fn update_vina_params(project_dir: String, vina_json: String) -> String {
    match run_backend_module_async(
        "dockstart_core.project",
        vec!["update-vina".to_string(), project_dir, vina_json],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法保存 Vina 参数。", &error),
    }
}

#[tauri::command]
async fn update_vina_run_protocol(
    project_dir: String,
    run_mode: String,
    autobox: bool,
    confirm_pose_context: Option<bool>,
) -> String {
    let task = tauri::async_runtime::spawn_blocking(move || -> Result<String, String> {
        let _guard_lock = project_run_guard_lock()
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let guard = inspect_project_run_guard(&project_dir, true)?;
        if guard.blocked {
            return Ok(project_run_guard_error_json(&guard));
        }
        run_backend_module(
            "dockstart_core.project",
            vec![
                "update-run-protocol".to_string(),
                project_dir,
                run_mode,
                autobox.to_string(),
                confirm_pose_context.unwrap_or(false).to_string(),
            ],
        )
    });
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => fallback_project_error_json("无法保存运行任务类型。", &error),
        Err(error) => {
            fallback_project_error_json("运行任务类型保存任务异常结束。", &error.to_string())
        }
    }
}

#[tauri::command]
async fn update_run_settings(
    project_dir: String,
    box_json: String,
    vina_json: String,
    run_mode: String,
    autobox: bool,
    confirm_pose_context: Option<bool>,
) -> String {
    let task = tauri::async_runtime::spawn_blocking(move || -> Result<String, String> {
        let _guard_lock = project_run_guard_lock()
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let guard = inspect_project_run_guard(&project_dir, true)?;
        if guard.blocked {
            return Ok(project_run_guard_error_json(&guard));
        }
        run_backend_module(
            "dockstart_core.project",
            vec![
                "update-run-settings".to_string(),
                project_dir,
                box_json,
                vina_json,
                run_mode,
                autobox.to_string(),
                confirm_pose_context.unwrap_or(false).to_string(),
            ],
        )
    });
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => fallback_project_error_json("无法原子保存运行设置。", &error),
        Err(error) => fallback_project_error_json("运行设置保存任务异常结束。", &error.to_string()),
    }
}

#[tauri::command]
async fn get_vina_config_preview(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.project",
        vec!["preview-config".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法生成 Vina 配置预览。", &error),
    }
}

#[tauri::command]
async fn generate_vina_config(project_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.project",
        vec!["generate-config".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法生成 vina_config.txt。", &error),
    }
}

#[tauri::command]
async fn get_autogrid_maps_defaults(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.autogrid",
        vec!["defaults".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 AutoGrid4 maps 默认参数。", &error),
    }
}

#[tauri::command]
async fn get_autogrid_maps_status(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.autogrid",
        vec!["status".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 AutoDock4 maps 状态。", &error),
    }
}

#[tauri::command]
async fn set_scoring_protocol(project_dir: String, protocol: String) -> String {
    match run_backend_module_async(
        "dockstart_core.autogrid",
        vec!["set-protocol".to_string(), project_dir, protocol],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法切换对接评分协议。", &error),
    }
}

#[tauri::command]
async fn get_ad4zn_status(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.ad4zn",
        vec!["status".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 AutoDock4Zn beta 状态。", &error),
    }
}

#[tauri::command]
async fn save_ad4zn_review(project_dir: String, review_json: String) -> String {
    match run_backend_module_async(
        "dockstart_core.ad4zn",
        vec!["review".to_string(), project_dir, review_json],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法保存 Zn 位点复核。", &error),
    }
}

#[tauri::command]
async fn prepare_ad4zn_receptor(project_dir: String, options_json: String) -> String {
    match run_backend_module_async(
        "dockstart_core.ad4zn",
        vec!["prepare".to_string(), project_dir, options_json],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法生成 AD4Zn TZ 受体。", &error),
    }
}

#[tauri::command]
async fn set_ad4zn_parameter_file(project_dir: String, parameter_file: String) -> String {
    match run_backend_module_async(
        "dockstart_core.ad4zn",
        vec!["parameter".to_string(), project_dir, parameter_file],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法校验并记录 AD4Zn.dat。", &error),
    }
}

#[tauri::command]
async fn generate_autogrid_maps(project_dir: String, options_json: String) -> String {
    match run_backend_module_async(
        "dockstart_core.autogrid",
        vec!["generate".to_string(), project_dir, options_json],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法生成 AutoDock4 affinity maps。", &error),
    }
}

#[tauri::command]
async fn import_autogrid_maps(project_dir: String, fld_file: String) -> String {
    match run_backend_module_async(
        "dockstart_core.autogrid",
        vec!["import".to_string(), project_dir, fld_file],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导入 AutoDock4 affinity maps。", &error),
    }
}

#[tauri::command]
async fn get_vina_maps_status(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.vina_maps",
        vec!["status".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 Vina maps 状态。", &error),
    }
}

#[tauri::command]
async fn set_vina_maps_mode(project_dir: String, mode: String) -> String {
    match run_backend_module_async(
        "dockstart_core.vina_maps",
        vec!["set-mode".to_string(), project_dir, mode],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法切换 Vina 网格来源。", &error),
    }
}

#[tauri::command]
async fn generate_vina_maps(project_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.vina_maps",
        vec!["generate".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法生成 Vina maps。", &error),
    }
}

#[tauri::command]
async fn import_vina_maps(
    project_dir: String,
    source_path: String,
    options_json: String,
) -> String {
    match run_backend_module_async(
        "dockstart_core.vina_maps",
        vec!["import".to_string(), project_dir, source_path, options_json],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导入 Vina maps。", &error),
    }
}

#[tauri::command]
async fn get_hydrated_status(project_dir: String) -> String {
    match run_backend_json_module_async(
        "dockstart_core.hydrated",
        vec!["status".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取实验性水合 AD4 状态。", &error),
    }
}

#[tauri::command]
async fn prepare_hydrated_ligand(project_dir: String) -> String {
    match run_backend_json_module_async(
        "dockstart_core.hydrated",
        vec!["prepare-ligand".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法准备水合配体。", &error),
    }
}

fn hydrated_generate_maps_args(project_dir: String, options_json: Option<String>) -> Vec<String> {
    let mut args = vec!["generate-maps".to_string(), project_dir];
    if let Some(options_json) = options_json.filter(|value| !value.trim().is_empty()) {
        args.push("--options-json".to_string());
        args.push(options_json);
    }
    args
}

#[tauri::command]
async fn generate_hydrated_maps(project_dir: String, options_json: Option<String>) -> String {
    let args = hydrated_generate_maps_args(project_dir, options_json);
    match run_backend_json_module_async("dockstart_core.hydrated", args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法生成实验性水合 AD4 maps。", &error),
    }
}

#[tauri::command]
async fn get_hydrated_run_preflight(project_dir: String) -> String {
    let project_dir_for_task = project_dir.clone();
    let task = tauri::async_runtime::spawn_blocking(move || -> Result<String, String> {
        let payload = run_backend_json_module(
            "dockstart_core.hydrated_run",
            vec!["preflight".to_string(), project_dir_for_task.clone()],
        )?;
        Ok(enrich_run_preflight_with_guard(
            &project_dir_for_task,
            &payload,
        ))
    });
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => {
            fallback_project_error_json("无法完成实验性水合 AD4 运行前检查。", &error)
        }
        Err(error) => fallback_project_error_json(
            "实验性水合 AD4 运行前检查任务异常结束。",
            &error.to_string(),
        ),
    }
}

#[tauri::command]
async fn prepare_hydrated_run(project_dir: String) -> String {
    let task = tauri::async_runtime::spawn_blocking(move || -> Result<String, String> {
        let _guard_lock = project_run_guard_lock()
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let guard = inspect_project_run_guard(&project_dir, true)?;
        if guard.blocked {
            return Ok(project_run_guard_error_json(&guard));
        }
        run_backend_json_module(
            "dockstart_core.hydrated_run",
            vec!["prepare".to_string(), project_dir],
        )
    });
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => fallback_project_error_json("无法准备实验性水合 AD4 run。", &error),
        Err(error) => {
            fallback_project_error_json("实验性水合 AD4 run 准备任务异常结束。", &error.to_string())
        }
    }
}

#[tauri::command]
async fn load_hydrated_results(project_dir: String, run_id: String) -> String {
    match run_backend_json_module_async(
        "dockstart_core.hydrated_run",
        vec!["results".to_string(), project_dir, run_id],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取实验性水合 AD4 结果。", &error),
    }
}

#[tauri::command]
async fn validate_run_prerequisites(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.project",
        vec!["validate-run".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法完成运行前检查。", &error),
    }
}

#[tauri::command]
async fn get_run_preflight(project_dir: String) -> String {
    let project_dir_for_task = project_dir.clone();
    let task = tauri::async_runtime::spawn_blocking(move || -> Result<String, String> {
        let payload = run_backend_module_uncached(
            "dockstart_core.project",
            vec!["run-preflight".to_string(), project_dir_for_task.clone()],
        )?;
        Ok::<String, String>(enrich_run_preflight_with_guard(
            &project_dir_for_task,
            &payload,
        ))
    });
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => fallback_project_error_json("无法完成运行驾驶舱检查。", &error),
        Err(error) => {
            fallback_project_error_json("运行驾驶舱检查任务异常结束。", &error.to_string())
        }
    }
}

#[tauri::command]
async fn get_project_run_guard(project_dir: String) -> String {
    let task = tauri::async_runtime::spawn_blocking(move || {
        inspect_project_run_guard(&project_dir, true).map(|guard| {
            serde_json::to_string(&guard).unwrap_or_else(|error| {
                fallback_project_error_json("无法序列化项目运行守卫。", &error.to_string())
            })
        })
    });
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => fallback_project_error_json("无法检查未完成的 Vina 运行。", &error),
        Err(error) => fallback_project_error_json("项目运行守卫任务异常结束。", &error.to_string()),
    }
}

#[tauri::command]
async fn prepare_vina_run(project_dir: String) -> String {
    let task = tauri::async_runtime::spawn_blocking(move || -> Result<String, String> {
        let _guard_lock = project_run_guard_lock()
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let guard = inspect_project_run_guard(&project_dir, true)?;
        if guard.blocked {
            return Ok(project_run_guard_error_json(&guard));
        }
        run_backend_module(
            "dockstart_core.project",
            vec!["prepare-run".to_string(), project_dir],
        )
    });
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => fallback_project_error_json("无法准备运行记录。", &error),
        Err(error) => fallback_project_error_json("运行记录准备任务异常结束。", &error.to_string()),
    }
}

fn multiple_ligand_prepare_args(
    project_dir: String,
    ligand_files: Vec<String>,
) -> Result<Vec<String>, String> {
    let ligand_files_json =
        serde_json::to_string(&ligand_files).map_err(|error| error.to_string())?;
    Ok(vec!["prepare".to_string(), project_dir, ligand_files_json])
}

#[tauri::command]
async fn prepare_multiple_ligand_run(project_dir: String, ligand_files: Vec<String>) -> String {
    let args = match multiple_ligand_prepare_args(project_dir.clone(), ligand_files) {
        Ok(args) => args,
        Err(error) => return fallback_project_error_json("无法序列化多配体共同对接输入。", &error),
    };
    let task = tauri::async_runtime::spawn_blocking(move || -> Result<String, String> {
        let _guard_lock = project_run_guard_lock()
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let guard = inspect_project_run_guard(&project_dir, true)?;
        if guard.blocked {
            return Ok(project_run_guard_error_json(&guard));
        }
        run_backend_module("dockstart_core.multiple_ligands", args)
    });
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => fallback_project_error_json("无法准备多配体共同对接运行记录。", &error),
        Err(error) => {
            fallback_project_error_json("多配体共同对接准备任务异常结束。", &error.to_string())
        }
    }
}

#[tauri::command]
async fn get_project_workflow_status(project_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.project",
        vec!["workflow-status".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取项目工作流状态。", &error),
    }
}

#[tauri::command]
async fn load_run_metadata(project_dir: String, run_id: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.project",
        vec!["load-run-metadata".to_string(), project_dir, run_id],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取运行元数据。", &error),
    }
}

#[tauri::command]
async fn execute_prepared_vina_run(project_dir: String, run_id: String) -> String {
    if let Err(error) = validate_run_directory(&project_dir, &run_id) {
        return fallback_project_error_json("运行目录校验失败。", &error);
    }
    let task = tauri::async_runtime::spawn_blocking(move || {
        let _guard_lock = project_run_guard_lock()
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let guard = inspect_project_run_guard(&project_dir, true)?;
        if guard.blocked {
            return Ok(project_run_guard_error_json(&guard));
        }
        run_backend_module(
            "dockstart_core.project",
            vec!["execute-run".to_string(), project_dir, run_id],
        )
    });
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => fallback_project_error_json("无法执行 prepared Vina run。", &error),
        Err(error) => {
            fallback_project_error_json("Vina 后台运行任务异常结束。", &error.to_string())
        }
    }
}

#[tauri::command]
async fn get_run_runtime_status(project_dir: String, run_id: String) -> String {
    if let Err(error) = validate_run_directory(&project_dir, &run_id) {
        return fallback_project_error_json("运行目录校验失败。", &error);
    }
    match run_backend_module_async(
        "dockstart_core.project",
        vec!["run-runtime-status".to_string(), project_dir, run_id],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 Vina 运行状态。", &error),
    }
}

#[tauri::command]
async fn get_multiple_ligand_run_status(project_dir: String, run_id: String) -> String {
    if let Err(error) = validate_run_directory(&project_dir, &run_id) {
        return fallback_project_error_json("多配体共同对接运行目录校验失败。", &error);
    }
    match run_backend_module_async(
        "dockstart_core.multiple_ligands",
        vec!["status".to_string(), project_dir, run_id],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取多配体共同对接状态。", &error),
    }
}

#[tauri::command]
async fn cancel_vina_run(project_dir: String, run_id: String) -> String {
    if let Err(error) = validate_run_directory(&project_dir, &run_id) {
        return fallback_project_error_json("运行目录校验失败。", &error);
    }
    let task = tauri::async_runtime::spawn_blocking(move || {
        run_backend_module(
            "dockstart_core.project",
            vec!["cancel-run".to_string(), project_dir, run_id],
        )
    });
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => fallback_project_error_json("无法取消 Vina 运行。", &error),
        Err(error) => fallback_project_error_json("Vina 取消任务异常结束。", &error.to_string()),
    }
}

#[tauri::command]
async fn cancel_multiple_ligand_run(project_dir: String, run_id: String) -> String {
    if let Err(error) = validate_run_directory(&project_dir, &run_id) {
        return fallback_project_error_json("多配体共同对接运行目录校验失败。", &error);
    }
    let task = tauri::async_runtime::spawn_blocking(move || {
        run_backend_module(
            "dockstart_core.multiple_ligands",
            vec!["cancel".to_string(), project_dir, run_id],
        )
    });
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => fallback_project_error_json("无法取消多配体共同对接。", &error),
        Err(error) => {
            fallback_project_error_json("多配体共同对接取消过程异常结束。", &error.to_string())
        }
    }
}

#[tauri::command]
async fn get_run_files_status(project_dir: String, run_id: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.project",
        vec!["run-files-status".to_string(), project_dir, run_id],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取运行文件状态。", &error),
    }
}

#[tauri::command]
async fn analyze_vina_run_results(project_dir: String, run_id: String) -> String {
    match run_backend_module_async(
        "dockstart_core.project",
        vec!["analyze-results".to_string(), project_dir, run_id],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法解析 Vina 结果。", &error),
    }
}

#[tauri::command]
async fn calculate_reference_ligand_rmsd(
    project_dir: String,
    run_id: String,
    mode: u32,
    reference_path: String,
) -> String {
    match run_backend_module_async(
        "dockstart_core.reference_rmsd",
        vec![
            "calculate".to_string(),
            project_dir,
            run_id,
            mode.to_string(),
            reference_path,
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法计算共晶参考 RMSD。", &error),
    }
}

#[tauri::command]
async fn load_scores_csv(project_dir: String, run_id: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.project",
        vec!["load-scores".to_string(), project_dir, run_id],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 scores.csv。", &error),
    }
}

#[tauri::command]
async fn load_vina_evaluation(project_dir: String, run_id: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.project",
        vec!["load-evaluation".to_string(), project_dir, run_id],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取姿势评价结果。", &error),
    }
}

#[tauri::command]
async fn export_markdown_report(project_dir: String, run_id: String) -> String {
    match run_backend_module_async(
        "dockstart_core.project",
        vec!["export-report".to_string(), project_dir, run_id],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导出 Markdown 报告。", &error),
    }
}

#[tauri::command]
async fn export_multiple_ligand_markdown_report(project_dir: String, run_id: String) -> String {
    if let Err(error) = validate_run_directory(&project_dir, &run_id) {
        return fallback_project_error_json("多配体共同对接运行目录校验失败。", &error);
    }
    match run_backend_module_async(
        "dockstart_core.multiple_ligands",
        vec!["report".to_string(), project_dir, run_id],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导出多配体共同对接 Markdown 报告。", &error),
    }
}

#[tauri::command]
async fn get_report_status(project_dir: String, run_id: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.project",
        vec!["report-status".to_string(), project_dir, run_id],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取报告状态。", &error),
    }
}

#[tauri::command]
fn open_result_output_directory(
    project_dir: String,
    target: String,
    run_id: Option<String>,
    relative_path: Option<String>,
) -> String {
    let directory = match resolve_result_output_directory(
        &project_dir,
        &target,
        run_id.as_deref(),
        relative_path.as_deref(),
    ) {
        Ok(directory) => directory,
        Err(error) => {
            return local_path_error_json(
                "LOCAL_DIRECTORY_PATH_UNSAFE",
                "无法打开本地目录。",
                &error,
                "请刷新结果；若文件已经移动，请重新生成对应输出。",
            )
        }
    };

    if let Err(error) = launch_local_directory(&directory) {
        return local_path_error_json(
            "LOCAL_DIRECTORY_OPEN_FAILED",
            "无法打开本地目录。",
            &error,
            "请确认 Windows 文件资源管理器可用，或复制路径后手动打开。",
        );
    }

    serde_json::json!({
        "ok": true,
        "directory": directory.to_string_lossy(),
        "message": if target == "result_sdf" {
            "已打开拓扑 SDF 所在目录。"
        } else {
            "已打开项目报告目录。"
        },
        "error": serde_json::Value::Null,
    })
    .to_string()
}

#[tauri::command]
fn read_project_markdown_report(project_dir: String, relative_path: String) -> String {
    let report_path = match resolve_project_markdown_report(&project_dir, &relative_path) {
        Ok(path) => path,
        Err(error) => {
            return local_path_error_json(
                "MARKDOWN_PREVIEW_PATH_UNSAFE",
                "无法读取 Markdown 报告预览。",
                &error,
                "请刷新报告状态；若报告已移动，请重新生成报告。",
            )
        }
    };

    let bytes = match fs::read(&report_path) {
        Ok(bytes) if bytes.len() as u64 <= MAX_MARKDOWN_PREVIEW_BYTES => bytes,
        Ok(bytes) => {
            return local_path_error_json(
                "MARKDOWN_PREVIEW_TOO_LARGE",
                "Markdown 报告过大，无法在应用内预览。",
                &format!("size_bytes={}", bytes.len()),
                "请从报告目录使用本地 Markdown 阅读器打开该文件。",
            )
        }
        Err(error) => {
            return local_path_error_json(
                "MARKDOWN_PREVIEW_READ_FAILED",
                "读取 Markdown 报告时发生错误。",
                &error.to_string(),
                "请确认报告文件可读，或重新生成报告。",
            )
        }
    };
    let content = match String::from_utf8(bytes) {
        Ok(content) => content,
        Err(error) => {
            return local_path_error_json(
                "MARKDOWN_PREVIEW_ENCODING_INVALID",
                "Markdown 报告不是有效的 UTF-8 文本。",
                &error.to_string(),
                "请保留原文件用于排查，并重新生成报告。",
            )
        }
    };

    let project_root = match fs::canonicalize(&project_dir) {
        Ok(path) => path,
        Err(error) => {
            return local_path_error_json(
                "MARKDOWN_PREVIEW_PROJECT_UNAVAILABLE",
                "项目目录不可访问。",
                &error.to_string(),
                "请重新打开项目后再查看报告。",
            )
        }
    };
    let normalized_relative = report_path
        .strip_prefix(&project_root)
        .unwrap_or(&report_path)
        .to_string_lossy()
        .replace('\\', "/");

    serde_json::json!({
        "ok": true,
        "relative_path": normalized_relative,
        "size_bytes": content.len(),
        "content": content,
        "message": "Markdown 报告已载入只读预览。",
        "error": serde_json::Value::Null,
    })
    .to_string()
}

#[tauri::command]
async fn get_viewer_file_status(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.viewer",
        vec!["file-status".to_string(), project_dir],
        VIEWER_CACHE_TTL,
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 3D Viewer 文件状态。", &error),
    }
}

#[tauri::command]
async fn load_structure_for_viewer(project_dir: String, file_kind: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.viewer",
        vec!["load-structure".to_string(), project_dir, file_kind],
        VIEWER_CACHE_TTL,
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 3D Viewer 结构文件。", &error),
    }
}

#[tauri::command]
async fn load_screening_candidate_for_viewer(
    project_dir: String,
    candidate_id: String,
    expected_revision_sha256: String,
) -> String {
    if !is_safe_screening_candidate_id(&candidate_id) {
        return screening_argument_error_json(
            "VIEWER_SCREENING_CANDIDATE_INVALID",
            "批量配体候选编号无效。",
            &candidate_id,
            "请从当前批量配体列表中重新选择。",
        );
    }
    if !is_sha256_hex(&expected_revision_sha256) {
        return screening_argument_error_json(
            "VIEWER_SCREENING_REVISION_INVALID",
            "批量配体导入 revision 无效。",
            &expected_revision_sha256,
            "请刷新批量配体列表后重新选择。",
        );
    }
    match run_backend_module_async(
        "dockstart_core.viewer",
        vec![
            "load-screening-candidate".to_string(),
            project_dir,
            candidate_id,
            expected_revision_sha256,
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取批量配体候选。", &error),
    }
}

#[tauri::command]
async fn list_docking_poses(project_dir: String, run_id: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.viewer",
        vec!["list-poses".to_string(), project_dir, run_id],
        VIEWER_CACHE_TTL,
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 docking pose 列表。", &error),
    }
}

#[tauri::command]
async fn load_docking_pose_for_viewer(
    project_dir: String,
    run_id: String,
    mode: Option<i32>,
    pose_kind: Option<String>,
) -> String {
    let mut args = vec!["load-pose".to_string(), project_dir, run_id];
    if let Some(value) = mode.or_else(|| pose_kind.as_ref().map(|_| 1)) {
        args.push(value.to_string());
    }
    if let Some(value) = pose_kind.filter(|value| !value.trim().is_empty()) {
        args.push(value);
    }
    match run_backend_module_cached_async("dockstart_core.viewer", args, VIEWER_CACHE_TTL).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 docking pose 内容。", &error),
    }
}

#[tauri::command]
async fn load_multiple_ligand_pose(
    project_dir: String,
    run_id: String,
    mode: i32,
    member_index: i32,
) -> String {
    if let Err(error) = validate_run_directory(&project_dir, &run_id) {
        return fallback_project_error_json("多配体共同对接运行目录校验失败。", &error);
    }
    match run_backend_module_async(
        "dockstart_core.multiple_ligands",
        vec![
            "load-pose".to_string(),
            project_dir,
            run_id,
            mode.to_string(),
            member_index.to_string(),
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取多配体联合构象。", &error),
    }
}

#[tauri::command]
async fn load_screening_pose_for_viewer(
    project_dir: String,
    item_id: String,
    mode: Option<i32>,
) -> String {
    let mut args = vec!["load-screening-pose".to_string(), project_dir, item_id];
    if let Some(value) = mode {
        args.push(value.to_string());
    }
    match run_backend_module_cached_async("dockstart_core.viewer", args, VIEWER_CACHE_TTL).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取批量筛选构象。", &error),
    }
}

#[tauri::command]
async fn load_archived_screening_pose_for_viewer(
    project_dir: String,
    archive_id: String,
    item_id: String,
    mode: Option<i32>,
) -> String {
    if !is_safe_screening_archive_id(&archive_id) {
        return screening_argument_error_json(
            "VIEWER_SCREENING_ARCHIVE_ID_INVALID",
            "批量筛选归档编号无效。",
            &archive_id,
            "请从历史筛选列表中重新选择归档。",
        );
    }
    if !is_safe_screening_item_id(&item_id) {
        return screening_argument_error_json(
            "VIEWER_SCREENING_ITEM_INVALID",
            "批量筛选配体编号无效。",
            &item_id,
            "请从所选历史筛选的结果表中重新选择配体。",
        );
    }
    if !is_safe_screening_pose_mode(mode) {
        return screening_argument_error_json(
            "VIEWER_SCREENING_MODE_INVALID",
            "批量筛选构象编号必须在 1 到 50 之间。",
            &mode.map(|value| value.to_string()).unwrap_or_default(),
            "请从该配体实际包含的构象编号中选择。",
        );
    }

    let mut args = vec![
        "load-screening-archive-pose".to_string(),
        project_dir,
        archive_id,
        item_id,
    ];
    if let Some(value) = mode {
        args.push(value.to_string());
    }
    // Archived structures are integrity-checked snapshots. Avoid the viewer
    // cache here so an external same-size file replacement cannot reuse an
    // earlier verified payload during the long viewer cache TTL.
    match run_backend_module_async("dockstart_core.viewer", args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取历史筛选构象。", &error),
    }
}

#[tauri::command]
async fn load_local_only_pose_pair_for_viewer(project_dir: String, run_id: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.viewer",
        vec!["load-local-pose-pair".to_string(), project_dir, run_id],
        VIEWER_CACHE_TTL,
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 local_only 优化前后姿势叠合。", &error),
    }
}

#[tauri::command]
async fn load_pose_score_summary(project_dir: String, run_id: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.viewer",
        vec!["score-summary".to_string(), project_dir, run_id],
        VIEWER_CACHE_TTL,
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 pose score 摘要。", &error),
    }
}

#[tauri::command]
async fn get_box_visualization(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.viewer",
        vec!["box-visualization".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 Box 可视化数据。", &error),
    }
}

#[tauri::command]
async fn update_box_from_visualization(project_dir: String, box_json: String) -> String {
    match run_backend_module_async(
        "dockstart_core.viewer",
        vec![
            "update-box-visualization".to_string(),
            project_dir,
            box_json,
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法保存 Box 可视化参数。", &error),
    }
}

#[tauri::command]
async fn stage_screening_inputs(project_dir: String, files: Vec<String>) -> String {
    let mut args = vec!["stage".to_string(), "--project".to_string(), project_dir];
    for file in files {
        args.push("--file".to_string());
        args.push(file);
    }
    match run_backend_module_async("dockstart_core.screening", args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导入批量筛选配体。", &error),
    }
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
async fn create_screening(
    project_dir: String,
    receptor_file: String,
    ligand_files: Vec<String>,
    ligand_candidate_ids: Option<Vec<String>>,
    expected_staging_revision_sha256: Option<String>,
    box_json: String,
    vina_json: String,
    max_retries: u32,
    top_n: u32,
) -> String {
    let mut args = vec![
        "create".to_string(),
        "--project".to_string(),
        project_dir,
        "--receptor".to_string(),
        receptor_file,
        "--box-json".to_string(),
        box_json,
        "--vina-json".to_string(),
        vina_json,
        "--max-retries".to_string(),
        max_retries.to_string(),
        "--top-n".to_string(),
        top_n.to_string(),
    ];
    for ligand in ligand_files {
        args.push("--ligand".to_string());
        args.push(ligand);
    }
    for candidate_id in ligand_candidate_ids.unwrap_or_default() {
        if !is_safe_screening_candidate_id(&candidate_id) {
            return screening_argument_error_json(
                "SCREENING_CANDIDATE_ID_INVALID",
                "批量配体候选编号无效。",
                &candidate_id,
                "请刷新导入预览后重新选择配体。",
            );
        }
        args.push("--candidate-id".to_string());
        args.push(candidate_id);
    }
    if let Some(revision) = expected_staging_revision_sha256 {
        if !revision.is_empty() {
            if !is_sha256_hex(&revision) {
                return screening_argument_error_json(
                    "SCREENING_STAGING_REVISION_INVALID",
                    "批量配体导入 revision 无效。",
                    &revision,
                    "请刷新导入预览后重试。",
                );
            }
            args.push("--staging-revision".to_string());
            args.push(revision);
        }
    }
    match run_backend_module_async("dockstart_core.screening", args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法创建批量筛选任务。", &error),
    }
}

#[tauri::command]
async fn retry_screening_preparation(
    project_dir: String,
    candidate_ids: Vec<String>,
    expected_staging_revision_sha256: String,
) -> String {
    if candidate_ids.is_empty()
        || candidate_ids
            .iter()
            .any(|candidate_id| !is_safe_screening_candidate_id(candidate_id))
    {
        return screening_argument_error_json(
            "SCREENING_CANDIDATE_ID_INVALID",
            "请选择至少一条有效的失败记录。",
            &candidate_ids.join(", "),
            "请刷新导入预览后重新选择可重试记录。",
        );
    }
    if !is_sha256_hex(&expected_staging_revision_sha256) {
        return screening_argument_error_json(
            "SCREENING_STAGING_REVISION_INVALID",
            "批量配体导入 revision 无效。",
            &expected_staging_revision_sha256,
            "请刷新导入预览后重试。",
        );
    }
    let mut args = vec![
        "retry-preparation".to_string(),
        "--project".to_string(),
        project_dir,
        "--staging-revision".to_string(),
        expected_staging_revision_sha256,
    ];
    for candidate_id in candidate_ids {
        args.push("--candidate-id".to_string());
        args.push(candidate_id);
    }
    match run_backend_module_async("dockstart_core.screening", args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法重试批量配体准备。", &error),
    }
}

#[tauri::command]
async fn get_screening_status(project_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.screening",
        vec!["status".to_string(), "--project".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取批量筛选状态。", &error),
    }
}

#[tauri::command]
async fn list_screening_archives(project_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.screening",
        vec!["archives".to_string(), "--project".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取批量筛选历史记录。", &error),
    }
}

#[tauri::command]
async fn get_screening_archive(project_dir: String, archive_id: String) -> String {
    if !is_safe_screening_archive_id(&archive_id) {
        return screening_argument_error_json(
            "SCREENING_ARCHIVE_ID_INVALID",
            "批量筛选归档编号无效。",
            &archive_id,
            "请从历史筛选列表中重新选择归档。",
        );
    }
    match run_backend_module_async(
        "dockstart_core.screening",
        vec![
            "archive-detail".to_string(),
            "--project".to_string(),
            project_dir,
            "--archive-id".to_string(),
            archive_id,
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取批量筛选历史详情。", &error),
    }
}

#[tauri::command]
async fn export_screening_archive_zip(
    project_dir: String,
    archive_id: String,
    output_path: String,
    overwrite: Option<bool>,
) -> String {
    if !is_safe_screening_archive_id(&archive_id) {
        return screening_argument_error_json(
            "SCREENING_ARCHIVE_ID_INVALID",
            "批量筛选归档编号无效。",
            &archive_id,
            "请从历史筛选列表中重新选择归档。",
        );
    }
    let args = match build_screening_archive_export_args(
        &project_dir,
        &archive_id,
        &output_path,
        overwrite,
    ) {
        Ok(args) => args,
        Err(ScreeningArchiveExportArgumentError::OverwriteUnsupported) => {
            return screening_archive_export_overwrite_error_json();
        }
    };
    match run_screening_archive_export_async(args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导出批量筛选归档 ZIP。", &error),
    }
}

#[tauri::command]
async fn compare_screening_archives(project_dir: String, archive_ids: Vec<String>) -> String {
    if !are_safe_screening_archive_comparison_ids(&archive_ids) {
        return screening_argument_error_json(
            "SCREENING_ARCHIVE_COMPARE_IDS_INVALID",
            "请选择两个不同且有效的批量筛选归档。",
            &archive_ids.join(", "),
            "请先选择基线归档，再选择一个对照归档。",
        );
    }
    let mut args = vec![
        "archive-compare".to_string(),
        "--project".to_string(),
        project_dir,
    ];
    for archive_id in archive_ids {
        args.push("--archive-id".to_string());
        args.push(archive_id);
    }
    match run_backend_module_async("dockstart_core.screening", args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法比较批量筛选历史归档。", &error),
    }
}

#[tauri::command]
async fn request_screening_cancel(project_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.screening",
        vec!["cancel".to_string(), "--project".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法请求取消批量筛选。", &error),
    }
}

#[tauri::command]
async fn resume_screening(project_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.screening",
        vec!["resume".to_string(), "--project".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法恢复批量筛选。", &error),
    }
}

#[tauri::command]
async fn archive_screening(project_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.screening",
        vec!["archive".to_string(), "--project".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法归档批量筛选。", &error),
    }
}

#[tauri::command]
async fn export_screening_report(project_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.screening",
        vec!["report".to_string(), "--project".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法生成批量筛选实验记录。", &error),
    }
}

#[tauri::command]
async fn generate_screening_result_sdf(project_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.screening",
        vec![
            "result-sdf".to_string(),
            "--project".to_string(),
            project_dir,
        ],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法生成批量结果 SDF。", &error),
    }
}

#[derive(Clone)]
struct BackgroundJobSpec {
    kind: String,
    key: String,
    module: String,
    args: Vec<String>,
    project_dir: String,
    run_id: String,
    target: String,
    fallback_message: String,
}

#[derive(Clone)]
struct QueuedBackgroundJob {
    app: tauri::AppHandle,
    task_id: String,
    spec: BackgroundJobSpec,
}

#[derive(Clone)]
struct BackgroundTaskRecord {
    task_id: String,
    kind: String,
    key: String,
    project_dir: String,
    project_key: String,
    run_id: String,
    target: String,
    status: String,
    stage: String,
    message: String,
    progress_percent: u8,
    progress_message: String,
    stdout_tail: String,
    stderr_tail: String,
    log_tail: String,
    created_at: Instant,
    started_at: Option<Instant>,
    finished_at: Option<Instant>,
    result_json: String,
    error: String,
    cancel_requested: bool,
}

#[derive(Default)]
struct BackgroundTaskRegistry {
    tasks: HashMap<String, BackgroundTaskRecord>,
    active_by_key: HashMap<String, String>,
    queue: VecDeque<QueuedBackgroundJob>,
    running_projects: HashSet<String>,
    running_count: usize,
}

static BACKGROUND_TASK_REGISTRY: OnceLock<(Mutex<BackgroundTaskRegistry>, Condvar)> =
    OnceLock::new();
static BACKGROUND_WORKERS_STARTED: OnceLock<()> = OnceLock::new();
static BACKGROUND_TASK_SEQUENCE: AtomicU64 = AtomicU64::new(1);
static PROJECT_RUN_GUARD_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

#[derive(Clone, Serialize)]
struct ProjectRunGuardItem {
    run_id: String,
    status: String,
    stage: String,
    process_active: bool,
    executor_active: bool,
    can_cancel: bool,
    registry_task_id: String,
    registry_status: String,
    message: String,
}

#[derive(Clone, Serialize)]
struct ProjectRunGuardPayload {
    ok: bool,
    blocked: bool,
    project_dir: String,
    active_runs: Vec<ProjectRunGuardItem>,
    message: String,
    error: String,
}

fn project_run_guard_lock() -> &'static Mutex<()> {
    PROJECT_RUN_GUARD_LOCK.get_or_init(|| Mutex::new(()))
}

fn background_task_registry() -> &'static (Mutex<BackgroundTaskRegistry>, Condvar) {
    BACKGROUND_TASK_REGISTRY.get_or_init(|| {
        (
            Mutex::new(BackgroundTaskRegistry::default()),
            Condvar::new(),
        )
    })
}

fn background_task_cancel_requested(task_id: &str) -> bool {
    let (mutex, _) = background_task_registry();
    let registry = mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    registry
        .tasks
        .get(task_id)
        .map(|record| record.cancel_requested)
        .unwrap_or(true)
}

fn project_run_guard_failure(project_dir: &str, error: &str) -> ProjectRunGuardPayload {
    ProjectRunGuardPayload {
        ok: false,
        blocked: true,
        project_dir: project_dir.to_string(),
        active_runs: Vec::new(),
        message: "无法可靠确认项目中是否仍有 Vina 进程；为避免重复运行，已暂时阻止新任务。"
            .to_string(),
        error: error.to_string(),
    }
}

fn inspect_project_run_guard(
    project_dir: &str,
    perform_recovery: bool,
) -> Result<ProjectRunGuardPayload, String> {
    let project_root = fs::canonicalize(project_dir)
        .map_err(|error| format!("项目目录不存在或不可访问：{error}"))?;
    let project_value = if perform_recovery {
        let payload = run_backend_module_uncached(
            "dockstart_core.project",
            vec![
                "recover-project".to_string(),
                project_root.to_string_lossy().into_owned(),
            ],
        )?;
        let value = serde_json::from_str::<serde_json::Value>(&payload)
            .map_err(|error| format!("项目恢复检查返回了无效 JSON：{error}"))?;
        if value.get("ok").and_then(serde_json::Value::as_bool) != Some(true) {
            let message = value
                .pointer("/error/message")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("项目恢复检查失败。");
            return Err(message.to_string());
        }
        value
            .get("project")
            .cloned()
            .ok_or_else(|| "项目恢复检查结果缺少 project 数据。".to_string())?
    } else {
        let content = fs::read_to_string(project_root.join("project.json"))
            .map_err(|error| format!("无法读取 project.json：{error}"))?;
        serde_json::from_str::<serde_json::Value>(&content)
            .map_err(|error| format!("project.json 不是有效 JSON：{error}"))?
    };

    let project_dir_text = project_root.to_string_lossy().into_owned();
    let mut active_runs = Vec::<ProjectRunGuardItem>::new();
    if let Some(runs) = project_value
        .get("runs")
        .and_then(serde_json::Value::as_array)
    {
        for run in runs {
            let summary_status = run
                .get("status")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("");
            if summary_status != "running" {
                continue;
            }
            let run_id = run
                .get("run_id")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("")
                .to_string();
            if !is_safe_run_id(&run_id) {
                return Err(format!(
                    "project.json 包含无效的 running run_id：{run_id}。"
                ));
            }
            validate_run_directory(&project_dir_text, &run_id)?;
            let runtime_payload = run_backend_module_uncached(
                "dockstart_core.project",
                vec![
                    "run-runtime-status".to_string(),
                    project_dir_text.clone(),
                    run_id.clone(),
                ],
            )?;
            let runtime = serde_json::from_str::<serde_json::Value>(&runtime_payload)
                .map_err(|error| format!("{run_id} 运行状态返回了无效 JSON：{error}"))?;
            let runtime_status = runtime
                .pointer("/metadata/status")
                .and_then(serde_json::Value::as_str)
                .unwrap_or(summary_status);
            if runtime_status != "running" {
                continue;
            }
            let stage = runtime
                .pointer("/metadata/stage")
                .and_then(serde_json::Value::as_str)
                .or_else(|| runtime.get("stage").and_then(serde_json::Value::as_str))
                .unwrap_or("running")
                .to_string();
            let process_active = runtime
                .get("process_active")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false);
            let executor_active = runtime
                .get("executor_active")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false);
            let runtime_ok = runtime.get("ok").and_then(serde_json::Value::as_bool) == Some(true);
            let runtime_error = runtime
                .pointer("/error/message")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("");
            active_runs.push(ProjectRunGuardItem {
                run_id,
                status: runtime_status.to_string(),
                stage,
                process_active,
                executor_active,
                can_cancel: true,
                registry_task_id: String::new(),
                registry_status: String::new(),
                message: if runtime_ok {
                    "检测到尚未结束的 Vina 运行。".to_string()
                } else if runtime_error.is_empty() {
                    "运行状态仍为 running，但进程身份尚未完全确认。".to_string()
                } else {
                    format!("运行状态仍为 running：{runtime_error}")
                },
            });
        }
    }

    let project_key = normalized_project_key(&project_dir_text);
    let (registry_mutex, _) = background_task_registry();
    let registry = registry_mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    for record in registry.tasks.values().filter(|record| {
        matches!(record.kind.as_str(), "vina" | "multiple-ligand")
            && record.project_key == project_key
            && !is_terminal_task_status(&record.status)
    }) {
        if let Some(item) = active_runs
            .iter_mut()
            .find(|item| item.run_id == record.run_id)
        {
            item.registry_task_id = record.task_id.clone();
            item.registry_status = record.status.clone();
            item.stage = monotonic_vina_task_stage(&item.stage, &record.status, &record.stage);
        } else {
            active_runs.push(ProjectRunGuardItem {
                run_id: record.run_id.clone(),
                status: record.status.clone(),
                stage: record.stage.clone(),
                process_active: record.status == "running",
                executor_active: record.status == "running",
                can_cancel: true,
                registry_task_id: record.task_id.clone(),
                registry_status: record.status.clone(),
                message: if record.status == "queued" {
                    "该 run 正在当前应用的后台队列中等待。".to_string()
                } else {
                    "该 run 正由当前应用的后台工作线程执行。".to_string()
                },
            });
        }
    }
    drop(registry);
    active_runs.sort_by(|left, right| left.run_id.cmp(&right.run_id));
    let blocked = !active_runs.is_empty();
    let message = if blocked {
        let ids = active_runs
            .iter()
            .map(|item| item.run_id.as_str())
            .collect::<Vec<_>>()
            .join("、");
        format!("检测到未完成运行 {ids}。请等待其结束，或打开运行详情安全取消后再创建新 run。")
    } else {
        "未检测到会与新任务冲突的 Vina 运行。".to_string()
    };
    Ok(ProjectRunGuardPayload {
        ok: true,
        blocked,
        project_dir: project_dir_text,
        active_runs,
        message,
        error: String::new(),
    })
}

fn project_run_guard_error_json(guard: &ProjectRunGuardPayload) -> String {
    let code = if guard.ok {
        "ACTIVE_VINA_RUN_DETECTED"
    } else {
        "RUN_RECOVERY_CHECK_FAILED"
    };
    serde_json::json!({
        "ok": false,
        "project_dir": guard.project_dir,
        "active_run_guard": guard,
        "message": guard.message,
        "error": {
            "code": code,
            "message": guard.message,
            "raw_error": guard.error,
            "suggestion": "请打开未完成 run 的详情页刷新状态；确认仍在运行时可使用“终止运行”，不要直接启动第二个 Vina 任务。",
        },
    })
    .to_string()
}

fn enrich_run_preflight_with_guard(project_dir: &str, payload: &str) -> String {
    let Ok(mut value) = serde_json::from_str::<serde_json::Value>(payload) else {
        return payload.to_string();
    };
    if value.get("ok").and_then(serde_json::Value::as_bool) != Some(true) {
        return payload.to_string();
    }
    let guard = inspect_project_run_guard(project_dir, false)
        .unwrap_or_else(|error| project_run_guard_failure(project_dir, &error));
    value["active_run_guard"] = serde_json::to_value(&guard).unwrap_or(serde_json::Value::Null);
    if guard.blocked {
        value["ready"] = serde_json::Value::Bool(false);
        if let Some(blockers) = value
            .get_mut("blockers")
            .and_then(serde_json::Value::as_array_mut)
        {
            blockers.push(serde_json::Value::String(guard.message.clone()));
        }
        if let Some(checks) = value
            .get_mut("checks")
            .and_then(serde_json::Value::as_array_mut)
        {
            checks.push(serde_json::json!({
                "key": "active_run",
                "name": "未完成运行",
                "status": "error",
                "message": guard.message,
                "detail": guard.error,
                "blocking": true,
                "action_page": "",
            }));
        }
        value["message"] = serde_json::Value::String(guard.message.clone());
    }
    value.to_string()
}

fn normalized_project_key(project_dir: &str) -> String {
    fs::canonicalize(project_dir)
        .unwrap_or_else(|_| PathBuf::from(project_dir))
        .to_string_lossy()
        .replace('/', "\\")
        .to_lowercase()
}

fn is_safe_run_id(run_id: &str) -> bool {
    if run_id.len() > 80 {
        return false;
    }
    let Some(sequence) = run_id.strip_prefix("run_") else {
        return false;
    };
    sequence.len() >= 3 && sequence.bytes().all(|value| value.is_ascii_digit())
}

fn is_safe_screening_item_id(item_id: &str) -> bool {
    if item_id.len() > 80 {
        return false;
    }
    let Some(sequence) = item_id.strip_prefix("ligand_") else {
        return false;
    };
    sequence.len() >= 4 && sequence.bytes().all(|value| value.is_ascii_digit())
}

fn is_safe_screening_candidate_id(candidate_id: &str) -> bool {
    let Some(value) = candidate_id.strip_prefix("ligand_") else {
        return false;
    };
    let parts = value.split('_').collect::<Vec<_>>();
    (parts.len() == 2 || parts.len() == 3)
        && parts[0].len() == 12
        && parts[0].bytes().all(|value| value.is_ascii_hexdigit())
        && parts[1].len() == 4
        && parts[1].bytes().all(|value| value.is_ascii_digit())
        && (parts.len() == 2
            || (parts[2].len() == 2 && parts[2].bytes().all(|value| value.is_ascii_digit())))
}

fn is_sha256_hex(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn is_safe_screening_archive_id(archive_id: &str) -> bool {
    if archive_id.len() > 96 {
        return false;
    }
    let Some(value) = archive_id.strip_prefix("screening_") else {
        return false;
    };
    let mut parts = value.split('_');
    let Some(sequence) = parts.next() else {
        return false;
    };
    let Some(timestamp) = parts.next() else {
        return false;
    };
    let collision_suffix = parts.next();
    if parts.next().is_some() {
        return false;
    }
    sequence.len() >= 3
        && sequence.bytes().all(|value| value.is_ascii_digit())
        && timestamp.len() == 14
        && timestamp.bytes().all(|value| value.is_ascii_digit())
        && match collision_suffix {
            None => true,
            Some(suffix) => suffix.len() >= 2 && suffix.bytes().all(|value| value.is_ascii_digit()),
        }
}

fn are_safe_screening_archive_comparison_ids(archive_ids: &[String]) -> bool {
    archive_ids.len() == 2
        && archive_ids[0] != archive_ids[1]
        && archive_ids
            .iter()
            .all(|archive_id| is_safe_screening_archive_id(archive_id))
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ScreeningArchiveExportArgumentError {
    OverwriteUnsupported,
}

fn build_screening_archive_export_args(
    project_dir: &str,
    archive_id: &str,
    output_path: &str,
    overwrite: Option<bool>,
) -> Result<Vec<String>, ScreeningArchiveExportArgumentError> {
    if overwrite == Some(true) {
        return Err(ScreeningArchiveExportArgumentError::OverwriteUnsupported);
    }
    Ok(vec![
        "archive-export".to_string(),
        "--project".to_string(),
        project_dir.to_string(),
        "--archive-id".to_string(),
        archive_id.to_string(),
        "--output".to_string(),
        output_path.to_string(),
    ])
}

fn screening_archive_export_overwrite_error_json() -> String {
    screening_argument_error_json(
        "SCREENING_ARCHIVE_EXPORT_OVERWRITE_UNSUPPORTED",
        "图形界面不支持覆盖已有的归档 ZIP。",
        "overwrite=true",
        "请选择新的文件名或新位置后再次导出。",
    )
}

fn is_safe_screening_pose_mode(mode: Option<i32>) -> bool {
    match mode {
        None => true,
        Some(value) => (1..=50).contains(&value),
    }
}

fn screening_argument_error_json(
    code: &str,
    message: &str,
    raw_error: &str,
    suggestion: &str,
) -> String {
    serde_json::json!({
        "ok": false,
        "screening": serde_json::Value::Null,
        "error": {
            "code": code,
            "title": message,
            "message": message,
            "raw_error": raw_error,
            "suggestion": suggestion,
        },
    })
    .to_string()
}

fn local_path_error_json(code: &str, message: &str, raw_error: &str, suggestion: &str) -> String {
    serde_json::json!({
        "ok": false,
        "directory": "",
        "relative_path": "",
        "content": "",
        "error": {
            "code": code,
            "title": message,
            "message": message,
            "raw_error": raw_error,
            "suggestion": suggestion,
        },
    })
    .to_string()
}

fn clean_project_relative_path(value: &str) -> Result<PathBuf, String> {
    let path = PathBuf::from(value);
    if value.trim().is_empty() || path.is_absolute() {
        return Err("只接受非空的项目内相对路径。".to_string());
    }
    if !path
        .components()
        .all(|component| matches!(component, Component::Normal(_)))
    {
        return Err("项目内路径不能包含盘符、根目录、当前目录或上级目录片段。".to_string());
    }
    Ok(path)
}

fn canonical_project_root(project_dir: &str) -> Result<PathBuf, String> {
    let root = fs::canonicalize(project_dir)
        .map_err(|error| format!("项目目录不存在或不可访问：{error}"))?;
    if !root.is_dir() {
        return Err("项目路径不是目录。".to_string());
    }

    let marker = root.join("project.json");
    let marker_metadata = fs::symlink_metadata(&marker)
        .map_err(|error| format!("项目目录缺少可读取的 project.json：{error}"))?;
    if marker_metadata.file_type().is_symlink() {
        return Err("project.json 不能是符号链接。".to_string());
    }
    let marker =
        fs::canonicalize(marker).map_err(|error| format!("project.json 不可访问：{error}"))?;
    if !marker.starts_with(&root) || !marker.is_file() {
        return Err("project.json 解析到了项目目录之外，或该路径不是文件。".to_string());
    }
    Ok(root)
}

fn canonical_project_subdirectory(project_root: &Path, name: &str) -> Result<PathBuf, String> {
    let requested = project_root.join(name);
    let metadata = fs::symlink_metadata(&requested)
        .map_err(|error| format!("项目 {name} 目录不存在或不可访问：{error}"))?;
    if metadata.file_type().is_symlink() {
        return Err(format!("项目 {name} 目录不能是符号链接。"));
    }
    let directory = fs::canonicalize(requested)
        .map_err(|error| format!("项目 {name} 目录不可访问：{error}"))?;
    if !directory.starts_with(project_root) || !directory.is_dir() {
        return Err(format!(
            "项目 {name} 目录解析到了项目目录之外，或该路径不是目录。"
        ));
    }
    Ok(directory)
}

fn resolve_result_output_directory(
    project_dir: &str,
    target: &str,
    run_id: Option<&str>,
    relative_path: Option<&str>,
) -> Result<PathBuf, String> {
    let project_root = canonical_project_root(project_dir)?;
    match target {
        "reports" => canonical_project_subdirectory(&project_root, "reports"),
        "result_sdf" => {
            let run_id = run_id.ok_or_else(|| "打开拓扑 SDF 目录需要 run_id。".to_string())?;
            let run_directory = validate_run_directory(project_dir, run_id)?;
            let exports_root = canonical_project_subdirectory(&run_directory, "exports")?;
            let relative = clean_project_relative_path(
                relative_path.ok_or_else(|| "尚未记录拓扑 SDF 输出文件。".to_string())?,
            )?;
            let requested = project_root.join(relative);
            let requested_metadata = fs::symlink_metadata(&requested)
                .map_err(|error| format!("拓扑 SDF 文件不存在或不可访问：{error}"))?;
            if requested_metadata.file_type().is_symlink() {
                return Err("拓扑 SDF 文件不能是符号链接。".to_string());
            }
            let output_file = fs::canonicalize(requested)
                .map_err(|error| format!("拓扑 SDF 文件不可访问：{error}"))?;
            let is_sdf = output_file
                .extension()
                .and_then(|value| value.to_str())
                .is_some_and(|value| value.eq_ignore_ascii_case("sdf"));
            if !output_file.starts_with(&exports_root) || !output_file.is_file() || !is_sdf {
                return Err(
                    "拓扑 SDF 必须是当前 run 的 exports 目录内已存在的 .sdf 文件。".to_string(),
                );
            }
            output_file
                .parent()
                .map(Path::to_path_buf)
                .ok_or_else(|| "无法解析拓扑 SDF 的父目录。".to_string())
        }
        _ => Err(format!("不支持的本地目录目标：{target}")),
    }
}

fn resolve_project_markdown_report(
    project_dir: &str,
    relative_path: &str,
) -> Result<PathBuf, String> {
    let project_root = canonical_project_root(project_dir)?;
    let reports_root = canonical_project_subdirectory(&project_root, "reports")?;
    let relative = clean_project_relative_path(relative_path)?;
    let requested = project_root.join(relative);
    let requested_metadata = fs::symlink_metadata(&requested)
        .map_err(|error| format!("Markdown 报告不存在或不可访问：{error}"))?;
    if requested_metadata.file_type().is_symlink() {
        return Err("Markdown 报告不能是符号链接。".to_string());
    }
    if requested_metadata.len() > MAX_MARKDOWN_PREVIEW_BYTES {
        return Err(format!(
            "Markdown 报告超过应用内预览上限：{} bytes。",
            requested_metadata.len()
        ));
    }
    let report =
        fs::canonicalize(requested).map_err(|error| format!("Markdown 报告不可访问：{error}"))?;
    let is_markdown = report
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("md"));
    if !report.starts_with(&reports_root) || !report.is_file() || !is_markdown {
        return Err("报告预览只允许读取项目 reports 目录内已存在的 .md 文件。".to_string());
    }
    Ok(report)
}

#[cfg(windows)]
fn launch_local_directory(directory: &Path) -> Result<(), String> {
    Command::new("explorer.exe")
        .arg(directory)
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("启动 Windows 文件资源管理器失败：{error}"))
}

#[cfg(target_os = "macos")]
fn launch_local_directory(directory: &Path) -> Result<(), String> {
    Command::new("open")
        .arg(directory)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("启动 Finder 失败：{error}"))
}

#[cfg(all(not(windows), not(target_os = "macos")))]
fn launch_local_directory(directory: &Path) -> Result<(), String> {
    Command::new("xdg-open")
        .arg(directory)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("启动文件管理器失败：{error}"))
}

fn validate_run_directory(project_dir: &str, run_id: &str) -> Result<PathBuf, String> {
    if !is_safe_run_id(run_id) {
        return Err(format!(
            "run_id 格式无效：{run_id}。应使用类似 run_001 的标识。"
        ));
    }
    let project_root = fs::canonicalize(project_dir)
        .map_err(|error| format!("项目目录不存在或不可访问：{error}"))?;
    let runs_root = fs::canonicalize(project_root.join("runs"))
        .map_err(|error| format!("项目 runs 目录不存在或不可访问：{error}"))?;
    if !runs_root.starts_with(&project_root) {
        return Err("项目 runs 目录解析到了项目目录之外。".to_string());
    }
    let run_directory = fs::canonicalize(runs_root.join(run_id))
        .map_err(|error| format!("运行目录不存在或不可访问：{error}"))?;
    if !run_directory.starts_with(&runs_root) || !run_directory.is_dir() {
        return Err("运行目录解析到了项目 runs 目录之外，或该路径不是目录。".to_string());
    }
    Ok(run_directory)
}

fn is_terminal_task_status(status: &str) -> bool {
    matches!(status, "finished" | "failed" | "cancelled")
}

#[derive(Clone, Serialize)]
struct BackgroundTaskProgressPayload {
    percent: u8,
    message: String,
}

#[derive(Clone, Serialize)]
struct BackgroundTaskPayload {
    ok: bool,
    task_id: String,
    kind: String,
    status: String,
    stage: String,
    project_dir: String,
    run_id: String,
    target: String,
    deduplicated: bool,
    progress: BackgroundTaskProgressPayload,
    elapsed_seconds: f64,
    message: String,
    stdout_tail: String,
    stderr_tail: String,
    log_tail: String,
    result_json: String,
    error: String,
}

fn background_task_payload(
    record: &BackgroundTaskRecord,
    deduplicated: bool,
) -> BackgroundTaskPayload {
    let elapsed_seconds = record
        .started_at
        .map(|started| {
            record
                .finished_at
                .unwrap_or_else(Instant::now)
                .saturating_duration_since(started)
                .as_secs_f64()
        })
        .unwrap_or(0.0);
    BackgroundTaskPayload {
        ok: true,
        task_id: record.task_id.clone(),
        kind: record.kind.clone(),
        status: record.status.clone(),
        stage: record.stage.clone(),
        project_dir: record.project_dir.clone(),
        run_id: record.run_id.clone(),
        target: record.target.clone(),
        deduplicated,
        progress: BackgroundTaskProgressPayload {
            percent: record.progress_percent,
            message: record.progress_message.clone(),
        },
        elapsed_seconds,
        message: record.message.clone(),
        stdout_tail: record.stdout_tail.clone(),
        stderr_tail: record.stderr_tail.clone(),
        log_tail: record.log_tail.clone(),
        result_json: record.result_json.clone(),
        error: record.error.clone(),
    }
}

fn background_task_status_json(record: &BackgroundTaskRecord, deduplicated: bool) -> String {
    serde_json::to_string(&background_task_payload(record, deduplicated)).unwrap_or_else(|error| {
        serde_json::json!({
            "ok": false,
            "task_id": record.task_id,
            "message": "无法序列化后台任务状态。",
            "error": error.to_string(),
        })
        .to_string()
    })
}

fn background_task_error_json(task_id: &str, message: &str, error: &str) -> String {
    serde_json::json!({
        "ok": false,
        "task_id": task_id,
        "message": message,
        "error": error,
    })
    .to_string()
}

fn emit_background_task(app: &tauri::AppHandle, record: &BackgroundTaskRecord) {
    let _ = app.emit(
        BACKGROUND_TASK_EVENT,
        background_task_payload(record, false),
    );
}

fn payload_reports_failure(payload: &str) -> bool {
    serde_json::from_str::<serde_json::Value>(payload)
        .ok()
        .and_then(|value| value.get("ok").and_then(serde_json::Value::as_bool))
        == Some(false)
}

fn payload_reports_cancelled(payload: &str) -> bool {
    let Ok(value) = serde_json::from_str::<serde_json::Value>(payload) else {
        return false;
    };
    let cancelled = [
        value.get("status"),
        value.get("stage"),
        value.pointer("/metadata/status"),
        value.pointer("/metadata/stage"),
        value.pointer("/screening/status"),
    ]
    .into_iter()
    .flatten()
    .filter_map(serde_json::Value::as_str)
    .any(|status| matches!(status, "cancelled" | "canceled"));
    cancelled
}

fn monotonic_vina_task_stage(current: &str, observed_status: &str, observed_stage: &str) -> String {
    if is_terminal_task_status(current) {
        return current.to_string();
    }
    let observed_is_cancelling = observed_status == "cancelling"
        || matches!(observed_stage, "cancel_pending" | "cancelling");
    if observed_is_cancelling || matches!(current, "cancel_pending" | "cancelling") {
        return "cancelling".to_string();
    }
    if matches!(observed_stage, "starting" | "running") {
        return observed_stage.to_string();
    }
    current.to_string()
}

fn read_file_tail(path: &Path, max_bytes: usize) -> String {
    if max_bytes == 0 {
        return String::new();
    }
    let Ok(mut file) = fs::File::open(path) else {
        return String::new();
    };
    let Ok(length) = file.metadata().map(|metadata| metadata.len()) else {
        return String::new();
    };
    let start = length.saturating_sub(max_bytes as u64);
    if file.seek(SeekFrom::Start(start)).is_err() {
        return String::new();
    }
    let mut content = Vec::with_capacity((length - start).min(max_bytes as u64) as usize);
    if file.read_to_end(&mut content).is_err() {
        return String::new();
    }
    String::from_utf8_lossy(&content).into_owned()
}

fn watch_vina_progress(
    app: tauri::AppHandle,
    task_id: String,
    project_dir: String,
    run_id: String,
    kind: String,
    stop: Arc<AtomicBool>,
) {
    let is_multiple_ligand = kind == "multiple-ligand";
    let run_dir = PathBuf::from(project_dir).join("runs").join(run_id);
    while !stop.load(Ordering::Acquire) {
        std::thread::sleep(Duration::from_millis(750));
        if stop.load(Ordering::Acquire) {
            break;
        }

        let metadata = fs::read_to_string(run_dir.join("metadata.json")).unwrap_or_default();
        let status = json_string_value(&metadata, "status").unwrap_or_default();
        let observed_stage = json_string_value(&metadata, "stage").unwrap_or_default();
        let stdout_tail = read_file_tail(&run_dir.join("stdout.txt"), 16 * 1024);
        let stderr_tail = read_file_tail(&run_dir.join("stderr.txt"), 16 * 1024);
        let log_tail = read_file_tail(&run_dir.join("log.txt"), 16 * 1024);
        let output_ready = fs::metadata(run_dir.join("out.pdbqt"))
            .map(|metadata| metadata.len() > 0)
            .unwrap_or(false);
        let cancelling = matches!(observed_stage.as_str(), "cancel_pending" | "cancelling")
            || status == "cancelling";
        let (percent, message) = if cancelling {
            if is_multiple_ligand {
                (0, "正在安全取消多配体共同对接；不会启动新的运行。")
            } else {
                (0, "正在安全取消 AutoDock Vina；不会启动新的运行。")
            }
        } else if output_ready {
            if is_multiple_ligand {
                (92, "联合构象输出已生成，正在核验两个成员与运行记录。")
            } else {
                (92, "Vina 已生成输出，正在收尾并核验记录。")
            }
        } else if log_tail.contains("mode |") || log_tail.contains("affinity") {
            if is_multiple_ligand {
                (82, "Vina 正在整理联合构象与整体评分。")
            } else {
                (82, "Vina 正在整理对接构象与评分。")
            }
        } else if log_tail.contains("Performing docking") || log_tail.contains("Computing") {
            if is_multiple_ligand {
                (55, "AutoDock Vina 正在搜索两个配体的联合构象空间。")
            } else {
                (55, "AutoDock Vina 正在搜索构象空间。")
            }
        } else if status == "running" {
            if is_multiple_ligand {
                (28, "多配体共同对接已启动，正在初始化联合搜索。")
            } else {
                (28, "AutoDock Vina 已启动，正在初始化计算。")
            }
        } else if is_multiple_ligand {
            (18, "正在等待多配体共同对接写入运行进度。")
        } else {
            (18, "正在等待 AutoDock Vina 写入运行进度。")
        };

        let record = {
            let (mutex, _) = background_task_registry();
            let mut registry = mutex
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            let Some(record) = registry.tasks.get_mut(&task_id) else {
                break;
            };
            if record.status != "running" {
                break;
            }
            let next_stage = monotonic_vina_task_stage(&record.stage, &status, &observed_stage);
            let stage_changed = next_stage != record.stage;
            record.stage = next_stage;
            let percent = if record.stage == "cancelling" {
                record.progress_percent
            } else {
                percent
            };
            if percent <= record.progress_percent
                && message == record.progress_message
                && stdout_tail == record.stdout_tail
                && stderr_tail == record.stderr_tail
                && log_tail == record.log_tail
                && !stage_changed
            {
                continue;
            }
            record.progress_percent = percent.max(record.progress_percent);
            record.progress_message = message.to_string();
            record.message = message.to_string();
            record.stdout_tail = stdout_tail;
            record.stderr_tail = stderr_tail;
            record.log_tail = log_tail;
            record.clone()
        };
        emit_background_task(&app, &record);
    }
}

fn background_worker_loop() {
    loop {
        let (job, running_record) = {
            let (mutex, wake) = background_task_registry();
            let mut registry = mutex
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            loop {
                let runnable_position = registry.queue.iter().position(|job| {
                    registry
                        .tasks
                        .get(&job.task_id)
                        .map(|record| {
                            record.status == "queued"
                                && !registry.running_projects.contains(&record.project_key)
                        })
                        .unwrap_or(false)
                });
                if let Some(position) = runnable_position {
                    let Some(job) = registry.queue.remove(position) else {
                        continue;
                    };
                    let Some(project_key) = registry
                        .tasks
                        .get(&job.task_id)
                        .map(|record| record.project_key.clone())
                    else {
                        continue;
                    };
                    registry.running_count += 1;
                    registry.running_projects.insert(project_key.clone());
                    let Some(record) = registry.tasks.get_mut(&job.task_id) else {
                        registry.running_count = registry.running_count.saturating_sub(1);
                        registry.running_projects.remove(&project_key);
                        continue;
                    };
                    record.status = "running".to_string();
                    record.stage = "running".to_string();
                    record.started_at = Some(Instant::now());
                    record.progress_percent = 12;
                    record.progress_message = match record.kind.as_str() {
                        "vina" => "正在启动 AutoDock Vina。",
                        "multiple-ligand" => "正在启动两个配体的共同对接。",
                        "structure-fetch" => "正在获取原始结构并校验下载内容。",
                        "screening" => "正在运行批量虚拟筛选队列。",
                        _ => "正在启动结构准备工具链。",
                    }
                    .to_string();
                    record.message = record.progress_message.clone();
                    break (job, record.clone());
                }
                registry = wake
                    .wait(registry)
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
            }
        };
        emit_background_task(&job.app, &running_record);
        run_background_job(job);
    }
}

fn ensure_background_workers() {
    BACKGROUND_WORKERS_STARTED.get_or_init(|| {
        for index in 0..MAX_CONCURRENT_BACKGROUND_TASKS {
            std::thread::Builder::new()
                .name(format!("dockstart-background-worker-{index}"))
                .spawn(background_worker_loop)
                .expect("failed to start DockStart background worker");
        }
    });
}

fn run_background_job(job: QueuedBackgroundJob) {
    let QueuedBackgroundJob { app, task_id, spec } = job;
    let watcher_stop = Arc::new(AtomicBool::new(false));
    let watcher = if matches!(spec.kind.as_str(), "vina" | "multiple-ligand") {
        let watcher_app = app.clone();
        let watcher_task_id = task_id.clone();
        let watcher_project_dir = spec.project_dir.clone();
        let watcher_run_id = spec.run_id.clone();
        let watcher_kind = spec.kind.clone();
        let watcher_stop_clone = Arc::clone(&watcher_stop);
        Some(std::thread::spawn(move || {
            watch_vina_progress(
                watcher_app,
                watcher_task_id,
                watcher_project_dir,
                watcher_run_id,
                watcher_kind,
                watcher_stop_clone,
            );
        }))
    } else {
        None
    };

    let result = if spec.kind == "preparation" {
        match cached_preparation_tools(&spec.project_dir) {
            Ok(tools) => {
                let tools_json = tools.to_string();
                run_backend_module_managed_with_env(
                    &task_id,
                    &spec.kind,
                    &spec.module,
                    spec.args.clone(),
                    &[(PREPARATION_TOOLS_SNAPSHOT_ENV_VAR, tools_json.as_str())],
                )
            }
            // A cache failure must not make preparation less reliable than
            // before. The backend can still perform its own fresh probe.
            Err(_) => run_backend_module_managed_with_env(
                &task_id,
                &spec.kind,
                &spec.module,
                spec.args.clone(),
                &[],
            ),
        }
    } else {
        run_backend_module_managed_with_env(
            &task_id,
            &spec.kind,
            &spec.module,
            spec.args.clone(),
            &[],
        )
    };
    watcher_stop.store(true, Ordering::Release);
    if let Some(watcher) = watcher {
        let _ = watcher.join();
    }

    let finished_record = {
        let (mutex, wake) = background_task_registry();
        let mut registry = mutex
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        registry.running_count = registry.running_count.saturating_sub(1);
        let project_key = registry
            .tasks
            .get(&task_id)
            .map(|record| record.project_key.clone())
            .unwrap_or_else(|| normalized_project_key(&spec.project_dir));
        registry.running_projects.remove(&project_key);
        registry.active_by_key.remove(&spec.key);
        let Some(record) = registry.tasks.get_mut(&task_id) else {
            wake.notify_all();
            return;
        };
        record.finished_at = Some(Instant::now());
        match result {
            Ok(payload) if payload_reports_cancelled(&payload) => {
                record.status = "cancelled".to_string();
                record.stage = "cancelled".to_string();
                record.progress_message = "任务已取消，已保留现有日志。".to_string();
                record.message = record.progress_message.clone();
                record.result_json = payload;
            }
            Ok(payload) if payload_reports_failure(&payload) => {
                record.status = "failed".to_string();
                record.stage = "failed".to_string();
                record.progress_message = spec.fallback_message.clone();
                record.message = record.progress_message.clone();
                record.result_json = payload;
            }
            Ok(payload) => {
                record.status = "finished".to_string();
                record.stage = "finished".to_string();
                record.progress_percent = 100;
                record.progress_message = match record.kind.as_str() {
                    "vina" => "AutoDock Vina 运行已结束。",
                    "multiple-ligand" => "多配体共同对接运行已结束。",
                    "structure-fetch" => "原始结构获取任务已结束。",
                    "screening" => "批量虚拟筛选队列已结束。",
                    _ => "结构准备任务已结束。",
                }
                .to_string();
                record.message = record.progress_message.clone();
                record.result_json = payload;
            }
            Err(ManagedProcessError::Cancelled) => {
                record.status = "cancelled".to_string();
                record.stage = "cancelled".to_string();
                record.progress_message = "任务已取消，后台工具进程树已经终止。".to_string();
                record.message = record.progress_message.clone();
            }
            Err(ManagedProcessError::TimedOut(timeout)) => {
                record.status = "failed".to_string();
                record.stage = "failed".to_string();
                record.progress_message = "后台工具运行超时，进程树已经终止。".to_string();
                record.message = record.progress_message.clone();
                record.error = ManagedProcessError::TimedOut(timeout).message();
            }
            Err(ManagedProcessError::Failed(error)) => {
                record.status = "failed".to_string();
                record.stage = "failed".to_string();
                record.progress_message = spec.fallback_message.clone();
                record.message = record.progress_message.clone();
                record.error = error;
            }
        }
        let finished = record.clone();
        wake.notify_all();
        finished
    };
    emit_background_task(&app, &finished_record);
}

fn start_background_job(app: tauri::AppHandle, spec: BackgroundJobSpec) -> String {
    ensure_background_workers();
    let task_id = {
        let (mutex, wake) = background_task_registry();
        let mut registry = mutex
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if registry.tasks.len() > 128 {
            let stale_ids = registry
                .tasks
                .iter()
                .filter(|(_, record)| {
                    is_terminal_task_status(&record.status)
                        && record.created_at.elapsed() > Duration::from_secs(5 * 60)
                })
                .map(|(task_id, _)| task_id.clone())
                .take(32)
                .collect::<Vec<_>>();
            for stale_id in stale_ids {
                registry.tasks.remove(&stale_id);
            }
        }
        if let Some(existing_id) = registry.active_by_key.get(&spec.key).cloned() {
            if let Some(existing) = registry.tasks.get(&existing_id) {
                return background_task_status_json(existing, true);
            }
        }
        if registry.queue.len() >= MAX_QUEUED_BACKGROUND_TASKS {
            return background_task_error_json(
                "",
                "后台任务队列已满，请等待当前任务完成后重试。",
                "BACKGROUND_QUEUE_FULL",
            );
        }

        let sequence = BACKGROUND_TASK_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let task_id = format!("task-{}-{sequence}", std::process::id());
        let record = BackgroundTaskRecord {
            task_id: task_id.clone(),
            kind: spec.kind.clone(),
            key: spec.key.clone(),
            project_dir: spec.project_dir.clone(),
            project_key: normalized_project_key(&spec.project_dir),
            run_id: spec.run_id.clone(),
            target: spec.target.clone(),
            status: "queued".to_string(),
            stage: "queued".to_string(),
            message: "任务已进入后台队列。".to_string(),
            progress_percent: 0,
            progress_message: "等待可用的后台执行槽位。".to_string(),
            stdout_tail: String::new(),
            stderr_tail: String::new(),
            log_tail: String::new(),
            created_at: Instant::now(),
            started_at: None,
            finished_at: None,
            result_json: String::new(),
            error: String::new(),
            cancel_requested: false,
        };
        registry
            .active_by_key
            .insert(spec.key.clone(), task_id.clone());
        registry.tasks.insert(task_id.clone(), record);
        registry.queue.push_back(QueuedBackgroundJob {
            app: app.clone(),
            task_id: task_id.clone(),
            spec: spec.clone(),
        });
        wake.notify_all();
        task_id
    };

    let queued_record = {
        let (mutex, _) = background_task_registry();
        let registry = mutex
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        registry.tasks[&task_id].clone()
    };
    emit_background_task(&app, &queued_record);
    background_task_status_json(&queued_record, false)
}

fn structure_fetch_task_key(project_key: &str, target: &str, request_parts: &[&str]) -> String {
    let mut hasher = DefaultHasher::new();
    for part in request_parts {
        part.hash(&mut hasher);
    }
    format!(
        "structure-fetch|{project_key}|{target}|{:016x}",
        hasher.finish()
    )
}

fn preparation_task_key(project_dir: &str, target: &str) -> String {
    let project_key = normalized_project_key(project_dir);
    let project_root = PathBuf::from(project_dir);
    let project_json = project_root.join("project.json");
    let mut hasher = DefaultHasher::new();
    target.hash(&mut hasher);

    if let Ok(content) = fs::read_to_string(&project_json) {
        if let Ok(project) = serde_json::from_str::<serde_json::Value>(&content) {
            let raw_file = project
                .get(target)
                .and_then(|value| value.get("raw_file"))
                .and_then(serde_json::Value::as_str)
                .unwrap_or("");
            raw_file.hash(&mut hasher);
            if !raw_file.trim().is_empty() {
                let relative = Path::new(raw_file);
                if !relative.is_absolute() {
                    let candidate = project_root.join(relative);
                    if let (Ok(canonical_root), Ok(canonical_candidate)) = (
                        fs::canonicalize(&project_root),
                        fs::canonicalize(&candidate),
                    ) {
                        if canonical_candidate.starts_with(&canonical_root) {
                            hash_path_signature(&mut hasher, &canonical_candidate, true);
                        }
                    }
                }
            }
        }
    }

    format!(
        "preparation|{project_key}|{target}|{:016x}",
        hasher.finish()
    )
}

fn preparation_task_key_with_options(
    project_dir: &str,
    target: &str,
    overwrite: bool,
    options_json: Option<&str>,
) -> String {
    let base_key = preparation_task_key(project_dir, target);
    let mut hasher = DefaultHasher::new();
    base_key.hash(&mut hasher);
    overwrite.hash(&mut hasher);
    match options_json
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        Some(options) => {
            let canonical = serde_json::from_str::<serde_json::Value>(options)
                .and_then(|value| serde_json::to_string(&value))
                .unwrap_or_else(|_| options.to_string());
            canonical.hash(&mut hasher);
        }
        None => "standard".hash(&mut hasher),
    }
    format!("{base_key}|{:016x}", hasher.finish())
}

#[tauri::command]
fn start_preparation_task(
    app: tauri::AppHandle,
    project_dir: String,
    target: String,
    overwrite: bool,
    options_json: Option<String>,
) -> String {
    let normalized_target = target.trim().to_lowercase();
    if !matches!(normalized_target.as_str(), "receptor" | "ligand") {
        return fallback_project_error_json(
            "无法创建结构准备任务：target 必须是 receptor 或 ligand。",
            &target,
        );
    }
    let command = if normalized_target == "receptor" {
        "prepare-receptor"
    } else {
        "prepare-ligand"
    };
    let task_key = preparation_task_key_with_options(
        &project_dir,
        &normalized_target,
        overwrite,
        options_json.as_deref(),
    );
    let mut args = vec![
        command.to_string(),
        project_dir.clone(),
        overwrite.to_string(),
    ];
    if let Some(options) = options_json.filter(|value| !value.trim().is_empty()) {
        args.push(options);
    }
    start_background_job(
        app,
        BackgroundJobSpec {
            kind: "preparation".to_string(),
            key: task_key,
            module: "dockstart_core.preparation".to_string(),
            args,
            project_dir,
            run_id: String::new(),
            target: normalized_target,
            fallback_message: "结构准备任务失败，请查看 preparation 日志。".to_string(),
        },
    )
}

#[tauri::command]
fn start_screening_task(app: tauri::AppHandle, project_dir: String) -> String {
    let project_key = normalized_project_key(&project_dir);
    start_background_job(
        app,
        BackgroundJobSpec {
            kind: "screening".to_string(),
            key: format!("screening|{project_key}"),
            module: "dockstart_core.screening".to_string(),
            args: vec![
                "run".to_string(),
                "--project".to_string(),
                project_dir.clone(),
            ],
            project_dir,
            run_id: String::new(),
            target: "screening".to_string(),
            fallback_message: "批量虚拟筛选失败，请查看 screening 记录。".to_string(),
        },
    )
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
fn start_flexible_receptor_task(
    app: tauri::AppHandle,
    project_dir: String,
    residues: Vec<String>,
    max_residues: u8,
    allow_bad_res: Option<bool>,
    acknowledged_bad_residues: Option<Vec<String>>,
    resolved_altlocs: Option<HashMap<String, String>>,
    selection_context_sha256: Option<String>,
) -> String {
    if residues.is_empty() {
        return fallback_project_error_json(
            "请至少选择一个柔性残基。",
            "residues must not be empty",
        );
    }
    let project_key = normalized_project_key(&project_dir);
    let mut normalized = residues
        .into_iter()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .collect::<Vec<_>>();
    normalized.sort();
    normalized.dedup();
    let allow_bad_res = allow_bad_res.unwrap_or(false);
    let mut acknowledged = acknowledged_bad_residues
        .unwrap_or_default()
        .into_iter()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .collect::<Vec<_>>();
    acknowledged.sort();
    acknowledged.dedup();
    let mut identity_args = Vec::new();
    if let Err(error) = append_flexible_identity_args(
        &mut identity_args,
        resolved_altlocs,
        selection_context_sha256,
    ) {
        return fallback_project_error_json("柔性残基身份参数无效。", &error);
    }
    let mut hasher = DefaultHasher::new();
    normalized.hash(&mut hasher);
    max_residues.hash(&mut hasher);
    allow_bad_res.hash(&mut hasher);
    acknowledged.hash(&mut hasher);
    identity_args.hash(&mut hasher);
    let mut args = vec![
        "prepare".to_string(),
        "--project".to_string(),
        project_dir.clone(),
        "--max-residues".to_string(),
        max_residues.to_string(),
    ];
    for residue in &normalized {
        args.extend(["--residue".to_string(), residue.clone()]);
    }
    args.extend(identity_args);
    if allow_bad_res {
        args.push("--allow-bad-res".to_string());
        for residue in &acknowledged {
            args.extend(["--acknowledge-bad-residue".to_string(), residue.clone()]);
        }
    }
    start_background_job(
        app,
        BackgroundJobSpec {
            kind: "flexible_receptor".to_string(),
            key: format!("flexible-receptor|{project_key}|{:016x}", hasher.finish()),
            module: "dockstart_core.flexible_receptor".to_string(),
            args,
            project_dir,
            run_id: String::new(),
            target: "receptor".to_string(),
            fallback_message: "柔性侧链受体准备失败，请查看 preparation/flexible_receptor 记录。"
                .to_string(),
        },
    )
}

#[tauri::command]
fn start_result_export_task(app: tauri::AppHandle, project_dir: String, run_id: String) -> String {
    let project_key = normalized_project_key(&project_dir);
    start_background_job(
        app,
        BackgroundJobSpec {
            kind: "result_export".to_string(),
            key: format!("result-export|{project_key}|{run_id}"),
            module: "dockstart_core.result_export".to_string(),
            args: vec![
                "export".to_string(),
                "--project".to_string(),
                project_dir.clone(),
                "--run".to_string(),
                run_id.clone(),
            ],
            project_dir,
            run_id,
            target: "result_sdf".to_string(),
            fallback_message: "Meeko SDF 导出失败，请查看 run exports 记录。".to_string(),
        },
    )
}

#[tauri::command]
fn start_pdb_fetch_task(
    app: tauri::AppHandle,
    project_dir: String,
    pdb_id: String,
    format: String,
    overwrite: bool,
) -> String {
    let project_key = normalized_project_key(&project_dir);
    let normalized_pdb_id = pdb_id.trim().to_uppercase();
    let normalized_format = format.trim().to_lowercase();
    let overwrite_key = overwrite.to_string();
    let task_key = structure_fetch_task_key(
        &project_key,
        "receptor",
        &[&normalized_pdb_id, &normalized_format, &overwrite_key],
    );
    start_background_job(
        app,
        BackgroundJobSpec {
            kind: "structure-fetch".to_string(),
            key: task_key,
            module: "dockstart_core.structure_fetch".to_string(),
            args: vec![
                "fetch-pdb".to_string(),
                project_dir.clone(),
                normalized_pdb_id,
                normalized_format,
                overwrite.to_string(),
            ],
            project_dir,
            run_id: String::new(),
            target: "receptor".to_string(),
            fallback_message: "RCSB PDB 原始结构获取失败，请查看错误详情。".to_string(),
        },
    )
}

#[tauri::command]
fn start_pubchem_fetch_task(
    app: tauri::AppHandle,
    project_dir: String,
    query: String,
    query_type: String,
    format: String,
    overwrite: bool,
) -> String {
    let project_key = normalized_project_key(&project_dir);
    let normalized_query_type = query_type.trim().to_lowercase();
    let request_query = query.trim().to_string();
    let normalized_query = if normalized_query_type == "cid" {
        request_query.clone()
    } else {
        request_query.to_lowercase()
    };
    let normalized_format = format.trim().to_lowercase();
    let overwrite_key = overwrite.to_string();
    let task_key = structure_fetch_task_key(
        &project_key,
        "ligand",
        &[
            &normalized_query_type,
            &normalized_query,
            &normalized_format,
            &overwrite_key,
        ],
    );
    start_background_job(
        app,
        BackgroundJobSpec {
            kind: "structure-fetch".to_string(),
            key: task_key,
            module: "dockstart_core.structure_fetch".to_string(),
            args: vec![
                "fetch-pubchem".to_string(),
                project_dir.clone(),
                request_query,
                normalized_format,
                overwrite.to_string(),
                normalized_query_type,
            ],
            project_dir,
            run_id: String::new(),
            target: "ligand".to_string(),
            fallback_message: "PubChem 原始配体获取失败，请查看错误详情。".to_string(),
        },
    )
}

#[tauri::command]
async fn start_vina_run_task(app: tauri::AppHandle, project_dir: String, run_id: String) -> String {
    if let Err(error) = validate_run_directory(&project_dir, &run_id) {
        return fallback_project_error_json("无法创建 Vina 后台任务：运行目录校验失败。", &error);
    }
    let task = tauri::async_runtime::spawn_blocking(move || -> Result<String, String> {
        let _guard_lock = project_run_guard_lock()
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let guard = inspect_project_run_guard(&project_dir, true)?;
        let only_same_in_memory_task = guard.blocked
            && guard
                .active_runs
                .iter()
                .all(|item| item.run_id == run_id && !item.registry_task_id.is_empty());
        if guard.blocked && !only_same_in_memory_task {
            return Ok(project_run_guard_error_json(&guard));
        }
        let project_key = normalized_project_key(&project_dir);
        Ok(start_background_job(
            app,
            BackgroundJobSpec {
                kind: "vina".to_string(),
                key: format!("vina|{project_key}|{}", run_id.to_lowercase()),
                module: "dockstart_core.project".to_string(),
                args: vec![
                    "execute-run".to_string(),
                    project_dir.clone(),
                    run_id.clone(),
                ],
                project_dir,
                run_id,
                target: String::new(),
                fallback_message: "AutoDock Vina 后台运行失败，请查看运行日志。".to_string(),
            },
        ))
    });
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => fallback_project_error_json("无法检查项目中的未完成运行。", &error),
        Err(error) => {
            fallback_project_error_json("Vina 后台任务创建过程异常结束。", &error.to_string())
        }
    }
}

#[tauri::command]
async fn start_multiple_ligand_task(
    app: tauri::AppHandle,
    project_dir: String,
    run_id: String,
) -> String {
    if let Err(error) = validate_run_directory(&project_dir, &run_id) {
        return fallback_project_error_json(
            "无法创建多配体共同对接后台任务：运行目录校验失败。",
            &error,
        );
    }
    let task = tauri::async_runtime::spawn_blocking(move || -> Result<String, String> {
        let _guard_lock = project_run_guard_lock()
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let guard = inspect_project_run_guard(&project_dir, true)?;
        let only_same_in_memory_task = guard.blocked
            && guard
                .active_runs
                .iter()
                .all(|item| item.run_id == run_id && !item.registry_task_id.is_empty());
        if guard.blocked && !only_same_in_memory_task {
            return Ok(project_run_guard_error_json(&guard));
        }
        let project_key = normalized_project_key(&project_dir);
        Ok(start_background_job(
            app,
            BackgroundJobSpec {
                kind: "multiple-ligand".to_string(),
                key: format!("multiple-ligand|{project_key}|{}", run_id.to_lowercase()),
                module: "dockstart_core.multiple_ligands".to_string(),
                args: vec!["run".to_string(), project_dir.clone(), run_id.clone()],
                project_dir,
                run_id,
                target: "simultaneous_multi_ligand".to_string(),
                fallback_message: "多配体共同对接后台运行失败，请查看运行日志与成员输入记录。"
                    .to_string(),
            },
        ))
    });
    match task.await {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => fallback_project_error_json("无法检查项目中的未完成运行。", &error),
        Err(error) => fallback_project_error_json(
            "多配体共同对接后台任务创建过程异常结束。",
            &error.to_string(),
        ),
    }
}

#[tauri::command]
fn get_background_task_status(task_id: String) -> String {
    let (mutex, _) = background_task_registry();
    let registry = mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    match registry.tasks.get(&task_id) {
        Some(record) => background_task_status_json(record, false),
        None => background_task_error_json(&task_id, "没有找到该后台任务。", "TASK_NOT_FOUND"),
    }
}

#[tauri::command]
fn find_active_background_task(
    project_dir: String,
    run_id: Option<String>,
    target: Option<String>,
    kind: Option<String>,
) -> String {
    let project_key = normalized_project_key(&project_dir);
    let run_id = run_id.unwrap_or_default();
    let target = target.unwrap_or_default();
    let kind = kind.unwrap_or_default();
    let (mutex, _) = background_task_registry();
    let registry = mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let active = registry
        .tasks
        .values()
        .filter(|record| {
            record.project_key == project_key
                && !is_terminal_task_status(&record.status)
                && (run_id.is_empty() || record.run_id == run_id)
                && (target.is_empty() || record.target == target)
                && (kind.is_empty() || record.kind == kind)
        })
        .max_by_key(|record| record.created_at);
    match active {
        Some(record) => background_task_status_json(record, false),
        None => background_task_error_json(
            "",
            "当前项目没有匹配的活动后台任务。",
            "ACTIVE_TASK_NOT_FOUND",
        ),
    }
}

#[tauri::command]
fn cancel_background_task(app: tauri::AppHandle, task_id: String) -> String {
    let cancelled_record = {
        let (mutex, wake) = background_task_registry();
        let mut registry = mutex
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let Some(snapshot) = registry.tasks.get(&task_id).cloned() else {
            return background_task_error_json(&task_id, "没有找到该后台任务。", "TASK_NOT_FOUND");
        };
        if is_terminal_task_status(&snapshot.status) {
            return background_task_status_json(&snapshot, false);
        }
        if snapshot.status == "queued" {
            registry.queue.retain(|job| job.task_id != task_id);
            registry.active_by_key.remove(&snapshot.key);
            let record = registry
                .tasks
                .get_mut(&task_id)
                .expect("queued task disappeared");
            record.status = "cancelled".to_string();
            record.stage = "cancelled".to_string();
            record.finished_at = Some(Instant::now());
            record.cancel_requested = true;
            record.message = "排队中的任务已取消。".to_string();
            record.progress_message = record.message.clone();
        } else {
            let record = registry
                .tasks
                .get_mut(&task_id)
                .expect("running task disappeared");
            record.cancel_requested = true;
            record.stage = "cancelling".to_string();
            record.message = "正在终止后台工具进程；现有日志和项目记录会保留。".to_string();
            record.progress_message = record.message.clone();
        }
        let cancelled = registry
            .tasks
            .get(&task_id)
            .expect("cancelled task disappeared")
            .clone();
        wake.notify_all();
        cancelled
    };
    emit_background_task(&app, &cancelled_record);
    background_task_status_json(&cancelled_record, false)
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct CacheSensitivity {
    runtime: bool,
    project: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CacheInvalidation {
    None,
    Runtime,
    Project,
    Both,
}

#[derive(Clone)]
struct CachedBackendResult {
    stored_at: Instant,
    last_accessed: Instant,
    size_bytes: usize,
    result: Result<String, String>,
    sensitivity: CacheSensitivity,
}

#[derive(Default)]
struct BackendReadCache {
    ready: HashMap<String, CachedBackendResult>,
    in_flight: HashSet<String>,
    runtime_generation: u64,
    project_generation: u64,
}

static BACKEND_READ_CACHE: OnceLock<(Mutex<BackendReadCache>, Condvar)> = OnceLock::new();

fn backend_read_cache() -> &'static (Mutex<BackendReadCache>, Condvar) {
    BACKEND_READ_CACHE.get_or_init(|| (Mutex::new(BackendReadCache::default()), Condvar::new()))
}

#[cfg(test)]
fn invalidate_backend_read_cache() {
    invalidate_backend_cache(CacheInvalidation::Both);
}

fn invalidate_backend_cache(scope: CacheInvalidation) {
    if scope == CacheInvalidation::None {
        return;
    }
    let (mutex, wake) = backend_read_cache();
    let mut state = mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    state.ready.retain(|_, entry| match scope {
        CacheInvalidation::Runtime => !entry.sensitivity.runtime,
        CacheInvalidation::Project => !entry.sensitivity.project,
        CacheInvalidation::Both => false,
        CacheInvalidation::None => true,
    });
    if matches!(scope, CacheInvalidation::Runtime | CacheInvalidation::Both) {
        state.runtime_generation = state.runtime_generation.wrapping_add(1);
    }
    if matches!(scope, CacheInvalidation::Project | CacheInvalidation::Both) {
        state.project_generation = state.project_generation.wrapping_add(1);
    }
    wake.notify_all();
}

fn cache_sensitivity(module: &str, args: &[String]) -> CacheSensitivity {
    let command = args.first().map(String::as_str).unwrap_or("");
    let runtime = matches!(
        module,
        "dockstart.runtime.preparation-tools"
            | "dockstart_core.tool_check"
            | "dockstart_core.toolchain"
            | "dockstart_core.toolchain_repair"
            | "dockstart_core.capabilities"
            | "dockstart_core.diagnostics"
    ) || (module == "dockstart_core.preparation" && command == "tool-status");
    let runtime_only_preparation_tools = module == "dockstart.runtime.preparation-tools"
        || (module == "dockstart_core.preparation" && command == "tool-status");
    let project = !runtime_only_preparation_tools
        && (!runtime
            || matches!(
                module,
                "dockstart_core.project"
                    | "dockstart_core.preparation"
                    | "dockstart_core.structure_fetch"
                    | "dockstart_core.viewer"
            )
            || (module == "dockstart_core.capabilities" && !args.is_empty()));
    CacheSensitivity { runtime, project }
}

fn cached_result_size(result: &Result<String, String>) -> usize {
    match result {
        Ok(payload) | Err(payload) => payload.len(),
    }
}

fn prune_backend_cache(state: &mut BackendReadCache) {
    prune_backend_cache_with_limits(
        state,
        MAX_BACKEND_CACHE_ENTRIES,
        MAX_BACKEND_CACHE_BYTES,
        MAX_BACKEND_CACHE_ENTRY_BYTES,
    );
}

fn prune_backend_cache_with_limits(
    state: &mut BackendReadCache,
    max_entries: usize,
    max_bytes: usize,
    max_entry_bytes: usize,
) {
    state.ready.retain(|_, entry| {
        entry.stored_at.elapsed() <= MAX_BACKEND_CACHE_AGE && entry.size_bytes <= max_entry_bytes
    });
    loop {
        let total_bytes = state
            .ready
            .values()
            .map(|entry| entry.size_bytes)
            .sum::<usize>();
        if state.ready.len() <= max_entries && total_bytes <= max_bytes {
            break;
        }
        let Some(oldest_key) = state
            .ready
            .iter()
            .min_by_key(|(_, entry)| entry.last_accessed)
            .map(|(key, _)| key.clone())
        else {
            break;
        };
        state.ready.remove(&oldest_key);
    }
}

fn hash_directory_file_signatures(hasher: &mut DefaultHasher, directory: &Path) {
    let Ok(entries) = fs::read_dir(directory) else {
        hash_path_signature(hasher, directory, false);
        return;
    };
    let mut files = entries
        .flatten()
        .filter_map(|entry| {
            entry
                .file_type()
                .ok()
                .filter(|kind| kind.is_file())
                .map(|_| entry.path())
        })
        .collect::<Vec<_>>();
    files.sort();
    for file in files {
        hash_path_signature(hasher, &file, false);
    }
}

fn hash_directory_tree_file_signatures(hasher: &mut DefaultHasher, directory: &Path) {
    let Ok(entries) = fs::read_dir(directory) else {
        hash_path_signature(hasher, directory, false);
        return;
    };
    let mut paths = entries
        .flatten()
        .map(|entry| entry.path())
        .collect::<Vec<_>>();
    paths.sort();
    for path in paths {
        let Ok(metadata) = fs::symlink_metadata(&path) else {
            hash_path_signature(hasher, &path, false);
            continue;
        };
        if metadata.file_type().is_symlink() || metadata.is_file() {
            hash_path_signature(hasher, &path, false);
        } else if metadata.is_dir() {
            hash_directory_tree_file_signatures(hasher, &path);
        }
    }
}

fn viewer_artifact_fingerprint(args: &[String]) -> String {
    let mut hasher = DefaultHasher::new();
    let command = args.first().map(String::as_str).unwrap_or("");
    let Some(project_dir) = args.get(1).map(PathBuf::from) else {
        return String::new();
    };
    command.hash(&mut hasher);
    hash_path_signature(&mut hasher, &project_dir.join("project.json"), true);

    match command {
        "load-screening-pose" => {
            let screening_dir = project_dir.join("screening");
            hash_path_signature(&mut hasher, &screening_dir.join("screening.json"), true);
            hash_path_signature(
                &mut hasher,
                &screening_dir.join("inputs").join("receptor.pdbqt"),
                false,
            );
            if let Some(item_id) = args
                .get(2)
                .filter(|item_id| is_safe_screening_item_id(item_id))
            {
                hash_directory_tree_file_signatures(
                    &mut hasher,
                    &screening_dir.join("attempts").join(item_id),
                );
            }
        }
        "load-pose" | "list-poses" | "score-summary" => {
            if let Some(run_id) = args.get(2) {
                let run_dir = project_dir.join("runs").join(run_id);
                hash_path_signature(&mut hasher, &run_dir.join("out.pdbqt"), false);
                hash_path_signature(&mut hasher, &run_dir.join("optimized.pdbqt"), false);
                hash_path_signature(
                    &mut hasher,
                    &run_dir.join("inputs").join("ligand.pdbqt"),
                    false,
                );
                hash_path_signature(&mut hasher, &run_dir.join("scores.csv"), false);
                hash_path_signature(&mut hasher, &run_dir.join("metadata.json"), true);
            }
        }
        "load-local-pose-pair" => {
            if let Some(run_id) = args.get(2) {
                let run_dir = project_dir.join("runs").join(run_id);
                hash_path_signature(
                    &mut hasher,
                    &run_dir.join("inputs").join("receptor.pdbqt"),
                    false,
                );
                hash_path_signature(
                    &mut hasher,
                    &run_dir.join("inputs").join("ligand.pdbqt"),
                    false,
                );
                hash_path_signature(
                    &mut hasher,
                    &run_dir.join("inputs").join("flex.pdbqt"),
                    false,
                );
                hash_path_signature(&mut hasher, &run_dir.join("optimized.pdbqt"), false);
                hash_path_signature(&mut hasher, &run_dir.join("metadata.json"), true);
            }
        }
        "load-structure" => {
            let file_kind = args.get(2).map(String::as_str).unwrap_or("");
            file_kind.hash(&mut hasher);
            match file_kind {
                "receptor_prepared" => hash_path_signature(
                    &mut hasher,
                    &project_dir.join("prepared").join("receptor.pdbqt"),
                    false,
                ),
                "ligand_prepared" => hash_path_signature(
                    &mut hasher,
                    &project_dir.join("prepared").join("ligand.pdbqt"),
                    false,
                ),
                "receptor_run" | "receptor_flex" => {
                    hash_directory_tree_file_signatures(
                        &mut hasher,
                        &project_dir.join("prepared").join("flexible_receptor"),
                    );
                    hash_path_signature(
                        &mut hasher,
                        &project_dir.join("prepared").join("receptor.pdbqt"),
                        false,
                    );
                }
                "receptor_raw" | "ligand_raw" => {
                    hash_directory_file_signatures(&mut hasher, &project_dir.join("raw"));
                }
                _ => {
                    hash_directory_file_signatures(&mut hasher, &project_dir.join("prepared"));
                    hash_directory_file_signatures(&mut hasher, &project_dir.join("raw"));
                }
            }
        }
        "file-status" => {
            hash_directory_file_signatures(&mut hasher, &project_dir.join("prepared"));
            hash_directory_file_signatures(&mut hasher, &project_dir.join("raw"));
            hash_path_signature(&mut hasher, &project_dir.join("runs"), false);
        }
        _ => {}
    }
    format!("{:016x}", hasher.finish())
}

fn project_artifact_fingerprint(module: &str, args: &[String]) -> String {
    let Some(project_dir) = args.get(1).map(PathBuf::from) else {
        return String::new();
    };
    let mut hasher = DefaultHasher::new();
    hash_path_signature(&mut hasher, &project_dir.join("project.json"), false);
    let command = args.first().map(String::as_str).unwrap_or("");
    command.hash(&mut hasher);

    match module {
        "dockstart_core.preparation" => {
            hash_directory_file_signatures(&mut hasher, &project_dir.join("raw"));
            hash_directory_file_signatures(&mut hasher, &project_dir.join("prepared"));
            hash_path_signature(&mut hasher, &project_dir.join("preparation"), false);
        }
        "dockstart_core.structure_fetch" => {
            hash_directory_file_signatures(&mut hasher, &project_dir.join("raw"));
        }
        "dockstart_core.project" => {
            if let Some(run_id) = args.get(2).filter(|run_id| is_safe_run_id(run_id)) {
                let run_dir = project_dir.join("runs").join(run_id);
                hash_path_signature(&mut hasher, &run_dir.join("metadata.json"), false);
                hash_path_signature(&mut hasher, &run_dir.join("out.pdbqt"), false);
                hash_path_signature(&mut hasher, &run_dir.join("optimized.pdbqt"), false);
                hash_path_signature(&mut hasher, &run_dir.join("scores.csv"), false);
                hash_path_signature(&mut hasher, &run_dir.join("evaluation.json"), false);
                hash_path_signature(&mut hasher, &run_dir.join("docking_report.md"), false);
                hash_path_signature(&mut hasher, &run_dir.join("evaluation_report.md"), false);
            } else {
                hash_path_signature(&mut hasher, &project_dir.join("runs"), false);
            }
        }
        _ => {}
    }
    format!("{:016x}", hasher.finish())
}

fn is_recovery_read(module: &str, args: &[String]) -> bool {
    let command = args.first().map(String::as_str).unwrap_or("");
    module == "dockstart_core.project"
        && matches!(
            command,
            "recover-project" | "run-preflight" | "workflow-status" | "run-runtime-status"
        )
}

fn backend_cache_key(module: &str, args: &[String], sensitivity: CacheSensitivity) -> String {
    let fingerprint = if sensitivity.runtime {
        runtime_fingerprint()
    } else {
        String::new()
    };
    let artifact_fingerprint = if module == "dockstart_core.viewer" {
        viewer_artifact_fingerprint(args)
    } else {
        String::new()
    };
    let project_fingerprint = if sensitivity.project {
        project_artifact_fingerprint(module, args)
    } else {
        String::new()
    };
    format!(
        "r{}p{}\u{1f}{fingerprint}\u{1f}{project_fingerprint}\u{1f}{artifact_fingerprint}\u{1f}{module}\u{1f}{}",
        u8::from(sensitivity.runtime),
        u8::from(sensitivity.project),
        args.join("\u{1f}")
    )
}

fn run_backend_module_cached(
    module: &str,
    args: Vec<String>,
    ttl: Duration,
) -> Result<String, String> {
    run_backend_module_cached_with(module, args, ttl, run_backend_module_uncached)
}

fn run_backend_module_cached_with<F>(
    module: &str,
    args: Vec<String>,
    ttl: Duration,
    runner: F,
) -> Result<String, String>
where
    F: FnOnce(&str, Vec<String>) -> Result<String, String>,
{
    if is_recovery_read(module, &args) {
        return runner(module, args);
    }
    let sensitivity = cache_sensitivity(module, &args);
    let key = backend_cache_key(module, &args, sensitivity);
    let (mutex, wake) = backend_read_cache();

    let request_generation = loop {
        let mut state = mutex
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        prune_backend_cache(&mut state);
        if let Some(entry) = state.ready.get_mut(&key) {
            if entry.stored_at.elapsed() <= ttl {
                entry.last_accessed = Instant::now();
                return entry.result.clone();
            }
        }
        if state.in_flight.contains(&key) {
            state = wake
                .wait(state)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            drop(state);
            continue;
        }
        state.in_flight.insert(key.clone());
        let generation = (state.runtime_generation, state.project_generation);
        drop(state);
        break generation;
    };

    let result = runner(module, args);
    let mut state = mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    state.in_flight.remove(&key);
    let runtime_is_current =
        !sensitivity.runtime || state.runtime_generation == request_generation.0;
    let project_is_current =
        !sensitivity.project || state.project_generation == request_generation.1;
    let size_bytes = cached_result_size(&result);
    if runtime_is_current && project_is_current && size_bytes <= MAX_BACKEND_CACHE_ENTRY_BYTES {
        let now = Instant::now();
        state.ready.insert(
            key,
            CachedBackendResult {
                stored_at: now,
                last_accessed: now,
                size_bytes,
                result: result.clone(),
                sensitivity,
            },
        );
        prune_backend_cache(&mut state);
    }
    wake.notify_all();
    result
}

fn backend_payload_value(payload: &str, operation: &str) -> Result<serde_json::Value, String> {
    let value = serde_json::from_str::<serde_json::Value>(payload)
        .map_err(|error| format!("{operation} 返回了无效 JSON：{error}"))?;
    if value.get("ok").and_then(serde_json::Value::as_bool) == Some(false) {
        let message = value
            .pointer("/error/message")
            .and_then(serde_json::Value::as_str)
            .or_else(|| value.get("message").and_then(serde_json::Value::as_str))
            .unwrap_or("本地操作未能完成。");
        let raw_error = value
            .pointer("/error/raw_error")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("");
        return Err(if raw_error.is_empty() {
            message.to_string()
        } else {
            format!("{message}\n{raw_error}")
        });
    }
    Ok(value)
}

fn load_project_snapshot_value(project_dir: &str) -> Result<serde_json::Value, String> {
    let payload = run_backend_module_uncached(
        "dockstart_core.project",
        vec!["load".to_string(), project_dir.to_string()],
    )?;
    backend_payload_value(&payload, "项目读取")
}

fn cached_preparation_tools(project_dir: &str) -> Result<serde_json::Value, String> {
    let project_dir = project_dir.to_string();
    let cached_project_dir = project_dir.clone();
    let payload = run_backend_module_cached_with(
        "dockstart.runtime.preparation-tools",
        Vec::new(),
        RUNTIME_CACHE_TTL,
        move |_, _| {
            let raw = run_backend_module_uncached(
                "dockstart_core.preparation",
                vec!["tool-status".to_string(), cached_project_dir],
            )?;
            let value = backend_payload_value(&raw, "准备工具能力检测")?;
            serde_json::to_string(value.get("tools").unwrap_or(&serde_json::Value::Null))
                .map_err(|error| error.to_string())
        },
    )?;
    let cached_tools = serde_json::from_str::<serde_json::Value>(&payload)
        .map_err(|error| format!("准备工具能力缓存损坏：{error}"))?;
    if preparation_tools_ready(&cached_tools) {
        return Ok(cached_tools);
    }

    // A cold-start import timeout must not remain sticky for the full runtime
    // cache TTL and block every later conversion attempt. Retry once without
    // the cache; genuine missing-tool results remain structured and visible.
    invalidate_backend_cache(CacheInvalidation::Runtime);
    let fresh_payload = run_backend_module_uncached(
        "dockstart_core.preparation",
        vec!["tool-status".to_string(), project_dir],
    )?;
    let fresh_value = backend_payload_value(&fresh_payload, "准备工具能力复检")?;
    Ok(fresh_value
        .get("tools")
        .cloned()
        .unwrap_or(serde_json::Value::Null))
}

fn preparation_tools_ready(tools: &serde_json::Value) -> bool {
    ["/python/status", "/rdkit/status", "/meeko/status"]
        .iter()
        .all(|pointer| tools.pointer(pointer).and_then(serde_json::Value::as_str) == Some("ok"))
}

fn safe_project_file_status(
    project_dir: &Path,
    relative_file: &str,
    key: &str,
    name: &str,
) -> serde_json::Value {
    if relative_file.trim().is_empty() {
        return serde_json::json!({
            "key": key,
            "name": name,
            "path": "",
            "exists": false,
            "is_file": false,
            "size": 0,
            "non_empty": false,
            "status": "missing",
            "message": format!("{name}尚未记录。"),
        });
    }

    let relative = Path::new(relative_file);
    let safe_components = !relative.is_absolute()
        && relative.components().all(|component| {
            matches!(
                component,
                std::path::Component::Normal(_) | std::path::Component::CurDir
            )
        });
    if !safe_components {
        return serde_json::json!({
            "key": key,
            "name": name,
            "path": relative_file,
            "absolute_path": "",
            "exists": false,
            "is_file": false,
            "size": 0,
            "non_empty": false,
            "status": "error",
            "message": format!("{name}路径不是安全的项目内相对路径。"),
        });
    }

    let candidate = project_dir.join(relative);
    let metadata = fs::symlink_metadata(&candidate).ok();
    let exists = metadata.is_some();
    let is_file = metadata
        .as_ref()
        .map(|item| item.is_file() && !item.file_type().is_symlink())
        .unwrap_or(false);
    let canonical_is_local = if exists {
        fs::canonicalize(&candidate)
            .map(|path| path.starts_with(project_dir))
            .unwrap_or(false)
    } else {
        true
    };
    let size = metadata
        .as_ref()
        .filter(|_| is_file && canonical_is_local)
        .map(|item| item.len())
        .unwrap_or(0);
    let non_empty = size > 0;
    let (status, message) = if !canonical_is_local {
        ("error", format!("{name}路径重解析到项目目录外。"))
    } else if !exists {
        ("missing", format!("{name}文件不存在。"))
    } else if !is_file {
        ("error", format!("{name}路径不是普通文件。"))
    } else if !non_empty {
        ("empty", format!("{name}文件为空。"))
    } else {
        ("ok", format!("{name}文件存在。"))
    };
    serde_json::json!({
        "key": key,
        "name": name,
        "path": relative_file,
        "absolute_path": candidate.to_string_lossy(),
        "exists": exists && canonical_is_local,
        "is_file": is_file && canonical_is_local,
        "size": size,
        "non_empty": non_empty,
        "status": status,
        "message": message,
    })
}

fn preparation_tool_status_snapshot(project_dir: &str) -> Result<String, String> {
    let loaded = load_project_snapshot_value(project_dir)?;
    let canonical_project_dir = loaded
        .get("project_dir")
        .and_then(serde_json::Value::as_str)
        .or_else(|| {
            loaded
                .pointer("/project/project_dir")
                .and_then(serde_json::Value::as_str)
        })
        .unwrap_or(project_dir);
    let tools = cached_preparation_tools(canonical_project_dir)?;
    Ok(serde_json::json!({
        "ok": true,
        "project_dir": canonical_project_dir,
        "python_path": tools.pointer("/python/path").and_then(serde_json::Value::as_str).unwrap_or(""),
        "python_source": tools.pointer("/python/source").and_then(serde_json::Value::as_str).unwrap_or("unknown"),
        "tools": tools,
        "message": "自动准备工具能力已从当前运行时快照读取。本阶段不执行分子处理。",
        "error": serde_json::Value::Null,
    })
    .to_string())
}

fn preparation_status_snapshot(project_dir: &str) -> Result<String, String> {
    let loaded = load_project_snapshot_value(project_dir)?;
    let project = loaded
        .get("project")
        .cloned()
        .ok_or_else(|| "项目读取结果缺少 project 数据。".to_string())?;
    let canonical_project_dir = project
        .get("project_dir")
        .and_then(serde_json::Value::as_str)
        .unwrap_or(project_dir)
        .to_string();
    let project_root = fs::canonicalize(&canonical_project_dir)
        .map_err(|error| format!("项目目录不可访问：{error}"))?;
    let project_path = |pointer: &str| {
        project
            .pointer(pointer)
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
    };
    let files = serde_json::json!({
        "receptor_raw": safe_project_file_status(&project_root, project_path("/receptor/raw_file"), "receptor_raw", "受体 raw 文件"),
        "ligand_raw": safe_project_file_status(&project_root, project_path("/ligand/raw_file"), "ligand_raw", "配体 raw 文件"),
        "receptor_prepared": safe_project_file_status(&project_root, project_path("/receptor/file"), "receptor_prepared", "受体 prepared PDBQT"),
        "ligand_prepared": safe_project_file_status(&project_root, project_path("/ligand/file"), "ligand_prepared", "配体 prepared PDBQT"),
    });
    let preparation = project
        .get("preparation")
        .cloned()
        .unwrap_or(serde_json::Value::Null);
    Ok(serde_json::json!({
        "ok": true,
        "project_dir": canonical_project_dir,
        "project": project,
        "preparation": preparation,
        // Tool probing imports RDKit and Meeko and can take several seconds on
        // a cold runtime. Keep page entry file-only; the UI requests the
        // cached tool snapshot explicitly or preparation validates it in the
        // background when conversion starts.
        "tools": serde_json::Value::Null,
        "files": files,
        "message": "PDBQT 自动准备状态已读取。",
        "error": serde_json::Value::Null,
    })
    .to_string())
}

async fn run_backend_module_async(
    module: &'static str,
    args: Vec<String>,
) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || run_backend_module(module, args))
        .await
        .map_err(|error| error.to_string())?
}

async fn run_backend_json_module_async(
    module: &'static str,
    args: Vec<String>,
) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || run_backend_json_module(module, args))
        .await
        .map_err(|error| error.to_string())?
}

async fn run_preparation_json_module_async(
    project_dir: String,
    module: &'static str,
    args: Vec<String>,
) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let python = cached_preparation_tools(&project_dir)
            .ok()
            .and_then(|tools| preparation_python_path(&tools));
        match python {
            Some(python) if Path::new(&python).is_file() => {
                run_backend_json_module_with_python(module, args, &python)
            }
            _ => run_backend_json_module(module, args),
        }
    })
    .await
    .map_err(|error| error.to_string())?
}

async fn run_screening_archive_export_async(args: Vec<String>) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || run_screening_archive_export(args))
        .await
        .map_err(|error| error.to_string())?
}

async fn run_backend_module_cached_async(
    module: &'static str,
    args: Vec<String>,
    ttl: Duration,
) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || run_backend_module_cached(module, args, ttl))
        .await
        .map_err(|error| error.to_string())?
}

fn run_backend_module(module: &str, args: Vec<String>) -> Result<String, String> {
    run_backend_module_with_env(module, args, &[])
}

fn run_backend_json_module(module: &str, args: Vec<String>) -> Result<String, String> {
    let invalidation = backend_command_invalidation(module, &args);
    invalidate_backend_cache(invalidation);
    let result = run_backend_json_module_uncached(module, args);
    invalidate_backend_cache(invalidation);
    result
}

fn preparation_python_path(tools: &serde_json::Value) -> Option<String> {
    let python = tools.get("python")?;
    if python.get("status").and_then(serde_json::Value::as_str) != Some("ok") {
        return None;
    }
    let path = python
        .get("path")
        .and_then(serde_json::Value::as_str)?
        .trim();
    (!path.is_empty()).then(|| path.to_string())
}

fn run_backend_json_module_with_python(
    module: &str,
    args: Vec<String>,
    python: &str,
) -> Result<String, String> {
    let invalidation = backend_command_invalidation(module, &args);
    invalidate_backend_cache(invalidation);
    let result = find_backend_dir()
        .ok_or_else(|| "未找到 DockStart 本地服务文件。请重新安装或恢复完整应用目录。".to_string())
        .and_then(|backend_dir| run_python_json_module(&backend_dir, python, module, &args));
    invalidate_backend_cache(invalidation);
    result
}

fn run_backend_module_with_env(
    module: &str,
    args: Vec<String>,
    extra_env: &[(&str, &str)],
) -> Result<String, String> {
    // Read-only invocations no longer flush the process cache. Mutations are
    // classified narrowly so project writes do not force expensive Python /
    // RDKit / Meeko probes to run again on the next page.
    let invalidation = backend_command_invalidation(module, &args);
    invalidate_backend_cache(invalidation);
    let result = run_backend_module_uncached_with_env(module, args, extra_env);
    invalidate_backend_cache(invalidation);
    result
}

fn run_backend_module_managed_with_env(
    task_id: &str,
    kind: &str,
    module: &str,
    args: Vec<String>,
    extra_env: &[(&str, &str)],
) -> Result<String, ManagedProcessError> {
    let invalidation = backend_command_invalidation(module, &args);
    invalidate_backend_cache(invalidation);
    let result = (|| {
        let backend_dir = find_backend_dir().ok_or_else(|| {
            ManagedProcessError::Failed(
                "未找到 DockStart 本地服务文件。请重新安装或恢复完整应用目录。".to_string(),
            )
        })?;
        let timeout = background_job_timeout(kind);
        let mut errors = Vec::new();
        for python in python_candidates(&backend_dir) {
            if background_task_cancel_requested(task_id) {
                return Err(ManagedProcessError::Cancelled);
            }
            let mut command = build_python_module_command_with_env(
                &backend_dir,
                &python,
                module,
                &args,
                extra_env,
            );
            #[cfg(windows)]
            command.creation_flags(CREATE_NO_WINDOW);
            match run_captured_command_managed(&mut command, timeout, || {
                background_task_cancel_requested(task_id)
            }) {
                Ok(output) => match classify_structured_backend_output(
                    output.status.success(),
                    &output.stdout,
                    &output.stderr,
                ) {
                    Ok(payload) => return Ok(payload),
                    Err(error) => errors.push(format!("{python}: {error}")),
                },
                Err(ManagedProcessError::Failed(error)) => {
                    errors.push(format!("{python}: {error}"));
                }
                Err(error @ ManagedProcessError::Cancelled)
                | Err(error @ ManagedProcessError::TimedOut(_)) => return Err(error),
            }
        }
        Err(ManagedProcessError::Failed(errors.join("\n")))
    })();
    invalidate_backend_cache(invalidation);
    result
}

fn run_backend_module_uncached(module: &str, args: Vec<String>) -> Result<String, String> {
    run_backend_module_uncached_with_env(module, args, &[])
}

fn run_backend_json_module_uncached(module: &str, args: Vec<String>) -> Result<String, String> {
    let backend_dir = find_backend_dir().ok_or_else(|| {
        "未找到 DockStart 本地服务文件。请重新安装或恢复完整应用目录。".to_string()
    })?;

    let mut errors = Vec::new();
    for python in python_candidates(&backend_dir) {
        match run_python_json_module(&backend_dir, &python, module, &args) {
            Ok(payload) => return Ok(payload),
            Err(error) => errors.push(format!("{python}: {error}")),
        }
    }
    Err(errors.join("\n"))
}

fn run_backend_module_uncached_with_env(
    module: &str,
    args: Vec<String>,
    extra_env: &[(&str, &str)],
) -> Result<String, String> {
    let backend_dir = find_backend_dir().ok_or_else(|| {
        "未找到 DockStart 本地服务文件。请重新安装或恢复完整应用目录。".to_string()
    })?;

    let mut errors = Vec::new();
    for python in python_candidates(&backend_dir) {
        match run_python_module_with_env(&backend_dir, &python, module, &args, extra_env) {
            Ok(payload) => return Ok(payload),
            Err(error) => errors.push(format!("{python}: {error}")),
        }
    }

    Err(errors.join("\n"))
}

fn run_screening_archive_export(args: Vec<String>) -> Result<String, String> {
    if args.first().map(String::as_str) != Some("archive-export") {
        return Err("批量筛选归档导出命令参数无效。".to_string());
    }
    let backend_dir = find_backend_dir().ok_or_else(|| {
        "未找到 DockStart 本地服务文件。请重新安装或恢复完整应用目录。".to_string()
    })?;
    let candidates = python_candidates(&backend_dir);
    run_screening_archive_export_candidates(candidates, |python| {
        run_python_screening_archive_export(&backend_dir, python, &args)
    })
}

fn run_screening_archive_export_candidates<F>(
    candidates: Vec<String>,
    mut runner: F,
) -> Result<String, String>
where
    F: FnMut(&str) -> Result<String, String>,
{
    let mut errors = Vec::new();
    for python in candidates {
        match runner(&python) {
            Ok(payload) => return Ok(payload),
            Err(error) => errors.push(format!("{python}: {error}")),
        }
    }
    Err(errors.join("\n"))
}

#[derive(Debug)]
struct CapturedProcessOutput {
    status: ExitStatus,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
}

#[derive(Debug, Eq, PartialEq)]
enum ManagedProcessError {
    Cancelled,
    TimedOut(Duration),
    Failed(String),
}

impl ManagedProcessError {
    fn message(&self) -> String {
        match self {
            Self::Cancelled => "后台任务已取消，工具进程树已经终止。".to_string(),
            Self::TimedOut(timeout) => format!(
                "后台工具超过 {} 秒仍未结束，已终止进程树。",
                timeout.as_secs()
            ),
            Self::Failed(error) => error.clone(),
        }
    }
}

fn read_process_pipe<T>(mut pipe: T) -> Result<Vec<u8>, String>
where
    T: Read,
{
    let mut content = Vec::new();
    pipe.read_to_end(&mut content)
        .map_err(|error| format!("无法读取后台工具输出：{error}"))?;
    Ok(content)
}

fn join_process_pipe(
    reader: Option<std::thread::JoinHandle<Result<Vec<u8>, String>>>,
) -> Result<Vec<u8>, ManagedProcessError> {
    match reader {
        Some(reader) => reader
            .join()
            .map_err(|_| ManagedProcessError::Failed("后台工具输出读取线程异常结束。".to_string()))?
            .map_err(ManagedProcessError::Failed),
        None => Ok(Vec::new()),
    }
}

fn finish_captured_process(
    status: ExitStatus,
    stdout_reader: Option<std::thread::JoinHandle<Result<Vec<u8>, String>>>,
    stderr_reader: Option<std::thread::JoinHandle<Result<Vec<u8>, String>>>,
) -> Result<CapturedProcessOutput, ManagedProcessError> {
    let stdout = join_process_pipe(stdout_reader)?;
    let stderr = join_process_pipe(stderr_reader)?;
    Ok(CapturedProcessOutput {
        status,
        stdout,
        stderr,
    })
}

fn run_captured_command_managed<F>(
    command: &mut Command,
    timeout: Duration,
    mut should_cancel: F,
) -> Result<CapturedProcessOutput, ManagedProcessError>
where
    F: FnMut() -> bool,
{
    command.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = command
        .spawn()
        .map_err(|error| ManagedProcessError::Failed(error.to_string()))?;
    let stdout_reader = child
        .stdout
        .take()
        .map(|stdout| std::thread::spawn(move || read_process_pipe(stdout)));
    let stderr_reader = child
        .stderr
        .take()
        .map(|stderr| std::thread::spawn(move || read_process_pipe(stderr)));
    let process_tree = match ProcessTreeGuard::attach(&child) {
        Ok(process_tree) => process_tree,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            let _ = join_process_pipe(stdout_reader);
            let _ = join_process_pipe(stderr_reader);
            return Err(ManagedProcessError::Failed(error));
        }
    };
    let started_at = Instant::now();

    loop {
        if should_cancel() {
            process_tree.terminate();
            let _ = child.kill();
            let _ = child.wait();
            let _ = join_process_pipe(stdout_reader);
            let _ = join_process_pipe(stderr_reader);
            return Err(ManagedProcessError::Cancelled);
        }
        if started_at.elapsed() >= timeout {
            process_tree.terminate();
            let _ = child.kill();
            let _ = child.wait();
            let _ = join_process_pipe(stdout_reader);
            let _ = join_process_pipe(stderr_reader);
            return Err(ManagedProcessError::TimedOut(timeout));
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                // The Python wrapper should not leave scientific-tool
                // descendants behind after returning. Terminating the now
                // otherwise-empty job closes that final lifecycle gap.
                process_tree.terminate();
                return finish_captured_process(status, stdout_reader, stderr_reader);
            }
            Ok(None) => std::thread::sleep(MANAGED_PROCESS_POLL_INTERVAL),
            Err(error) => {
                process_tree.terminate();
                let _ = child.kill();
                let _ = child.wait();
                let _ = join_process_pipe(stdout_reader);
                let _ = join_process_pipe(stderr_reader);
                return Err(ManagedProcessError::Failed(error.to_string()));
            }
        }
    }
}

fn backend_module_timeout(module: &str, args: &[String]) -> Duration {
    let command = args.first().map(String::as_str).unwrap_or("");
    if (module == "dockstart_core.project" && command == "execute-run")
        || (module == "dockstart_core.multiple_ligands" && command == "run")
        || (module == "dockstart_core.screening" && command == "run")
    {
        Duration::from_secs(48 * 60 * 60)
    } else if module == "dockstart_core.preparation"
        && matches!(command, "prepare-ligand" | "prepare-receptor")
        || module == "dockstart_core.flexible_receptor" && command == "prepare"
    {
        Duration::from_secs(6 * 60 * 60)
    } else if module == "dockstart_core.structure_fetch"
        && matches!(command, "fetch-pdb" | "fetch-pubchem" | "preview")
    {
        Duration::from_secs(20 * 60)
    } else {
        Duration::from_secs(2 * 60 * 60)
    }
}

fn background_job_timeout(kind: &str) -> Duration {
    match kind {
        "vina" | "multiple-ligand" | "screening" => Duration::from_secs(48 * 60 * 60),
        "preparation" | "flexible_receptor" => Duration::from_secs(6 * 60 * 60),
        "structure-fetch" => Duration::from_secs(20 * 60),
        _ => Duration::from_secs(2 * 60 * 60),
    }
}

fn run_python_screening_archive_export(
    backend_dir: &Path,
    python: &str,
    args: &[String],
) -> Result<String, String> {
    let mut command = build_python_module_command_with_env(
        backend_dir,
        python,
        "dockstart_core.screening",
        args,
        &[],
    );

    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    let output = run_captured_command_managed(
        &mut command,
        backend_module_timeout("dockstart_core.screening", args),
        || false,
    )
    .map_err(|error| error.message())?;
    classify_screening_archive_export_output(
        output.status.success(),
        &output.stdout,
        &output.stderr,
    )
}

fn run_python_json_module(
    backend_dir: &Path,
    python: &str,
    module: &str,
    args: &[String],
) -> Result<String, String> {
    let mut command = build_python_module_command_with_env(backend_dir, python, module, args, &[]);

    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    let output =
        run_captured_command_managed(&mut command, backend_module_timeout(module, args), || false)
            .map_err(|error| error.message())?;
    classify_structured_backend_output(output.status.success(), &output.stdout, &output.stderr)
}

fn classify_structured_backend_output(
    status_success: bool,
    stdout: &[u8],
    stderr: &[u8],
) -> Result<String, String> {
    if status_success {
        return String::from_utf8(stdout.to_vec()).map_err(|error| error.to_string());
    }
    if let Some(payload) = structured_business_error_payload(stdout) {
        return Ok(payload);
    }

    let stdout = String::from_utf8_lossy(stdout);
    let stderr = String::from_utf8_lossy(stderr);
    Err(format!("stdout:\n{stdout}\nstderr:\n{stderr}"))
}

fn classify_screening_archive_export_output(
    status_success: bool,
    stdout: &[u8],
    stderr: &[u8],
) -> Result<String, String> {
    classify_structured_backend_output(status_success, stdout, stderr)
}

fn structured_business_error_payload(stdout: &[u8]) -> Option<String> {
    let payload = std::str::from_utf8(stdout).ok()?;
    let value = serde_json::from_str::<serde_json::Value>(payload).ok()?;
    let object = value.as_object()?;
    if object.get("ok") == Some(&serde_json::Value::Bool(false)) {
        Some(payload.to_string())
    } else {
        None
    }
}

fn run_python_module_with_env(
    backend_dir: &Path,
    python: &str,
    module: &str,
    args: &[String],
    extra_env: &[(&str, &str)],
) -> Result<String, String> {
    let mut command =
        build_python_module_command_with_env(backend_dir, python, module, args, extra_env);

    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    let output =
        run_captured_command_managed(&mut command, backend_module_timeout(module, args), || false)
            .map_err(|error| error.message())?;
    classify_structured_backend_output(output.status.success(), &output.stdout, &output.stderr)
}

#[cfg(test)]
fn build_python_module_command(
    backend_dir: &Path,
    python: &str,
    module: &str,
    args: &[String],
) -> Command {
    build_python_module_command_with_env(backend_dir, python, module, args, &[])
}

fn build_python_module_command_with_env(
    backend_dir: &Path,
    python: &str,
    module: &str,
    args: &[String],
    extra_env: &[(&str, &str)],
) -> Command {
    let mut command = Command::new(python);
    command
        .arg("-B")
        .arg("-m")
        .arg(module)
        .args(args)
        .current_dir(backend_dir)
        .env("PYTHONIOENCODING", "utf-8")
        // `-B` protects the immediate backend interpreter. The environment
        // variable is inherited by Python probes launched from the backend,
        // so neither the packaged backend nor the bundled standard library is
        // mutated with __pycache__ directories after installation.
        .env("PYTHONDONTWRITEBYTECODE", "1");
    for (key, value) in extra_env {
        command.env(key, value);
    }
    command
}

fn python_candidates(backend_dir: &Path) -> Vec<String> {
    let mut candidates = Vec::new();

    for candidate in bundled_python_candidates(backend_dir) {
        push_python_candidate(&mut candidates, candidate);
    }

    if let Some(configured_python) = configured_python_from_settings() {
        push_python_candidate(&mut candidates, PathBuf::from(configured_python));
    }

    #[cfg(debug_assertions)]
    for candidate in dev_python3_discovery_candidates() {
        push_python_candidate(&mut candidates, candidate);
    }

    candidates.push("python".to_string());
    candidates.push("python3".to_string());
    candidates
}

/// Development-only discovery of a usable Python 3 interpreter.
///
/// In a development checkout there is neither a bundled Python nor a settings
/// file, so `python_candidates` would otherwise fall back to the bare `python`
/// / `python3` commands. On machines that also carry AutoDockTools / MGLTools,
/// the bare `python` command can resolve to the bundled Python 2.7, which
/// cannot run this Python 3-only backend. Prefer the Windows Python Launcher
/// (`py -3`), which resolves a Python 3 interpreter regardless of PATH order.
#[cfg(debug_assertions)]
fn dev_python3_discovery_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();

    // 1. Windows Python Launcher: `py -3` resolves an actual Python 3 binary.
    #[cfg(windows)]
    if let Some(path) = python3_via_py_launcher() {
        candidates.push(path);
    }

    // 2. Fall back to Python 3 executables that happen to be on PATH. This is
    //    a best-effort, version-agnostic probe; it never selects a specific
    //    hard-coded installation path.
    for name in ["python3", "python3.exe"] {
        if let Some(path) = first_executable_on_path(&[name]) {
            candidates.push(path);
        }
    }

    candidates
}

/// Ask the Windows Python Launcher (`py -3`) for the absolute path of the
/// Python 3 interpreter it would run. Returns `None` when the launcher is
/// missing, fails, or reports no usable interpreter.
#[cfg(all(debug_assertions, windows))]
fn python3_via_py_launcher() -> Option<PathBuf> {
    let mut launcher = None;
    if let Some(windows_dir) = env::var_os("WINDIR") {
        let candidate = PathBuf::from(&windows_dir).join("py.exe");
        if candidate.is_file() {
            launcher = Some(candidate);
        }
    }
    if launcher.is_none() {
        launcher = first_executable_on_path(&["py.exe", "py"]);
    }
    let launcher = launcher?;

    let output = Command::new(&launcher)
        .args(["-3", "-c", "import sys; print(sys.executable)"])
        .env("PYTHONIOENCODING", "utf-8")
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let stdout = String::from_utf8(output.stdout).ok()?;
    let executable = stdout.trim();
    if executable.is_empty() {
        return None;
    }
    let path = PathBuf::from(executable);
    path.is_file().then_some(path)
}

fn bundled_python_candidates(backend_dir: &Path) -> Vec<PathBuf> {
    let mut candidates = Vec::new();

    if let Ok(resource_dir) = env::var(RESOURCE_DIR_ENV_VAR) {
        let resource_dir = PathBuf::from(resource_dir);
        candidates.push(
            resource_dir
                .join("resources")
                .join("python")
                .join("python.exe"),
        );
        candidates.push(resource_dir.join("python").join("python.exe"));
    }

    if let Some(root) = backend_dir.parent() {
        candidates.push(root.join("resources").join("python").join("python.exe"));
    }

    candidates
}

fn push_python_candidate(candidates: &mut Vec<String>, candidate: PathBuf) {
    if !candidate.is_file() {
        return;
    }

    let text = candidate.to_string_lossy().to_string();
    if !candidates.iter().any(|existing| existing == &text) {
        candidates.push(text);
    }
}

fn configured_python_from_settings() -> Option<String> {
    configured_tool_from_settings("python")
}

fn effective_settings_path() -> Option<PathBuf> {
    if let Ok(configured_path) = env::var(SETTINGS_ENV_VAR) {
        if !configured_path.trim().is_empty() {
            return Some(PathBuf::from(configured_path));
        }
    }
    find_backend_dir()?
        .parent()
        .map(|root| root.join("dockstart_settings.json"))
}

fn configured_tool_from_settings(tool_key: &str) -> Option<String> {
    let settings_path = effective_settings_path()?;
    let content = fs::read_to_string(settings_path).ok()?;
    let value = json_string_value(&content, tool_key)?;
    if value.trim().is_empty() {
        None
    } else {
        Some(value)
    }
}

fn hash_path_signature(hasher: &mut DefaultHasher, path: &Path, hash_contents: bool) {
    path.to_string_lossy().to_lowercase().hash(hasher);
    let Ok(metadata) = fs::metadata(path) else {
        false.hash(hasher);
        return;
    };
    true.hash(hasher);
    metadata.len().hash(hasher);
    metadata.is_file().hash(hasher);
    if let Ok(modified) = metadata.modified() {
        if let Ok(value) = modified.duration_since(std::time::UNIX_EPOCH) {
            value.as_nanos().hash(hasher);
        }
    }
    if hash_contents && metadata.is_file() && metadata.len() <= 2 * 1024 * 1024 {
        if let Ok(content) = fs::read(path) {
            content.hash(hasher);
        }
    }
}

fn hash_python_package_metadata(hasher: &mut DefaultHasher, python_path: &Path) {
    let Some(runtime_root) = python_path.parent() else {
        return;
    };
    let site_packages = runtime_root.join("Lib").join("site-packages");
    let Ok(entries) = fs::read_dir(&site_packages) else {
        return;
    };
    let mut package_signatures = entries
        .flatten()
        .flat_map(|entry| {
            let name = entry.file_name().to_string_lossy().to_lowercase();
            if (name.starts_with("meeko-") || name.starts_with("rdkit-"))
                && name.ends_with(".dist-info")
            {
                vec![
                    (entry.path().join("METADATA"), true),
                    (entry.path().join("RECORD"), true),
                ]
            } else {
                Vec::new()
            }
        })
        .collect::<Vec<_>>();
    package_signatures.extend(
        [
            ("meeko/__init__.py", true),
            ("meeko/preparation.py", true),
            ("meeko/receptor_pdbqt.py", true),
            ("meeko/cli/mk_prepare_ligand.py", true),
            ("meeko/cli/mk_prepare_receptor.py", true),
            ("rdkit/__init__.py", true),
            ("rdkit/Chem/__init__.py", true),
            ("rdkit/Chem/AllChem.py", true),
            ("rdkit/rdBase.pyd", false),
            ("rdkit/Chem/rdchem.pyd", false),
            ("rdkit/Chem/rdMolDescriptors.pyd", false),
        ]
        .into_iter()
        .map(|(relative_path, hash_contents)| (site_packages.join(relative_path), hash_contents)),
    );
    package_signatures.sort_by(|left, right| left.0.cmp(&right.0));
    for (path, hash_contents) in package_signatures {
        hash_path_signature(hasher, &path, hash_contents);
    }
}

fn first_executable_on_path(names: &[&str]) -> Option<PathBuf> {
    let search_path = env::var_os("PATH")?;
    for directory in env::split_paths(&search_path) {
        for name in names {
            let candidate = directory.join(name);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

/// Build a cheap process-local fingerprint of the effective scientific
/// runtime. Small control files are content-hashed; large executables use
/// path/size/mtime while the manifest contributes their recorded SHA256.
fn runtime_fingerprint() -> String {
    let mut hasher = DefaultHasher::new();
    RESOURCE_DIR_ENV_VAR.hash(&mut hasher);
    env::var(RESOURCE_DIR_ENV_VAR)
        .unwrap_or_default()
        .hash(&mut hasher);
    SETTINGS_ENV_VAR.hash(&mut hasher);
    env::var(SETTINGS_ENV_VAR)
        .unwrap_or_default()
        .hash(&mut hasher);
    env::var_os("PATH")
        .unwrap_or_default()
        .to_string_lossy()
        .hash(&mut hasher);

    if let Some(settings_path) = effective_settings_path() {
        hash_path_signature(&mut hasher, &settings_path, true);
    }

    let mut roots = Vec::new();
    if let Ok(resource_dir) = env::var(RESOURCE_DIR_ENV_VAR) {
        let resource_dir = PathBuf::from(resource_dir);
        roots.push(resource_dir.join("resources"));
        roots.push(resource_dir);
    }
    if let Some(backend_dir) = find_backend_dir() {
        if let Some(repo_root) = backend_dir.parent() {
            roots.push(repo_root.join("resources"));
        }
    }
    roots.sort();
    roots.dedup();

    let mut python_paths = Vec::new();
    for root in &roots {
        hash_path_signature(&mut hasher, &root.join("toolchain_manifest.json"), true);
        let python_path = root.join("python").join("python.exe");
        hash_path_signature(&mut hasher, &python_path, false);
        python_paths.push(python_path);
        hash_path_signature(&mut hasher, &root.join("vina").join("vina.exe"), false);
        hash_path_signature(
            &mut hasher,
            &root.join("tools").join("vina").join("vina.exe"),
            false,
        );
    }

    if let Some(configured_python) = configured_tool_from_settings("python") {
        let path = PathBuf::from(configured_python);
        hash_path_signature(&mut hasher, &path, false);
        python_paths.push(path);
    }
    if let Some(configured_vina) = configured_tool_from_settings("vina") {
        hash_path_signature(&mut hasher, Path::new(&configured_vina), false);
    }
    if let Some(configured_autogrid) = configured_tool_from_settings("autogrid4") {
        hash_path_signature(&mut hasher, Path::new(&configured_autogrid), false);
    }
    if let Some(path_python) =
        first_executable_on_path(&["python.exe", "python", "python3.exe", "python3"])
    {
        hash_path_signature(&mut hasher, &path_python, false);
        python_paths.push(path_python);
    }
    if let Some(path_vina) = first_executable_on_path(&["vina.exe", "vina"]) {
        hash_path_signature(&mut hasher, &path_vina, false);
    }
    if let Some(path_autogrid) = first_executable_on_path(&["autogrid4.exe", "autogrid4"]) {
        hash_path_signature(&mut hasher, &path_autogrid, false);
    }
    python_paths.sort();
    python_paths.dedup();
    for python_path in python_paths {
        hash_python_package_metadata(&mut hasher, &python_path);
    }

    format!("{:016x}", hasher.finish())
}

fn backend_command_invalidation(module: &str, args: &[String]) -> CacheInvalidation {
    let command = args.first().map(String::as_str).unwrap_or("");
    match module {
        "dockstart_core.settings" if matches!(command, "save-json" | "update-tool-path") => {
            CacheInvalidation::Both
        }
        "dockstart_core.demo_projects" if command == "create" => CacheInvalidation::Project,
        "dockstart_core.structure_fetch"
            if matches!(
                command,
                "fetch-pdb"
                    | "fetch-pubchem"
                    | "import-receptor-raw"
                    | "import-ligand-raw"
                    | "clear-receptor-raw"
                    | "clear-ligand-raw"
            ) =>
        {
            CacheInvalidation::Project
        }
        "dockstart_core.preparation"
            if matches!(command, "prepare-ligand" | "prepare-receptor" | "reset") =>
        {
            CacheInvalidation::Project
        }
        "dockstart_core.macrocycle" if matches!(command, "review" | "confirm" | "reset") => {
            CacheInvalidation::Project
        }
        "dockstart_core.viewer" if command == "update-box-visualization" => {
            CacheInvalidation::Project
        }
        "dockstart_core.reference_rmsd" if command == "calculate" => CacheInvalidation::Project,
        "dockstart_core.autogrid" if matches!(command, "set-protocol" | "generate" | "import") => {
            CacheInvalidation::Project
        }
        "dockstart_core.ad4zn" if matches!(command, "review" | "prepare" | "parameter") => {
            CacheInvalidation::Project
        }
        "dockstart_core.vina_maps" if matches!(command, "set-mode" | "generate" | "import") => {
            CacheInvalidation::Project
        }
        "dockstart_core.hydrated" if matches!(command, "prepare-ligand" | "generate-maps") => {
            CacheInvalidation::Project
        }
        "dockstart_core.hydrated_run" if command == "prepare" => CacheInvalidation::Project,
        "dockstart_core.screening"
            if matches!(
                command,
                "stage" | "create" | "run" | "cancel" | "resume" | "archive" | "report"
            ) =>
        {
            CacheInvalidation::Project
        }
        "dockstart_core.multiple_ligands"
            if matches!(command, "prepare" | "run" | "cancel" | "report") =>
        {
            CacheInvalidation::Project
        }
        "dockstart_core.project"
            if matches!(
                command,
                "create"
                    | "import-receptor"
                    | "import-ligand"
                    | "update-box"
                    | "update-vina"
                    | "update-run-settings"
                    | "update-run-protocol"
                    | "generate-config"
                    | "prepare-run"
                    | "execute-run"
                    | "run-runtime-status"
                    | "cancel-run"
                    | "analyze-results"
                    | "export-report"
            ) =>
        {
            CacheInvalidation::Project
        }
        _ => CacheInvalidation::None,
    }
}

fn json_string_value(content: &str, key: &str) -> Option<String> {
    let needle = format!("\"{key}\"");
    let key_index = content.find(&needle)?;
    let after_key = &content[key_index + needle.len()..];
    let colon_index = after_key.find(':')?;
    let after_colon = after_key[colon_index + 1..].trim_start();
    let mut chars = after_colon.chars();
    if chars.next()? != '"' {
        return None;
    }

    let mut value = String::new();
    let mut escaped = false;
    for character in chars {
        if escaped {
            match character {
                '"' => value.push('"'),
                '\\' => value.push('\\'),
                '/' => value.push('/'),
                'b' => value.push('\u{0008}'),
                'f' => value.push('\u{000c}'),
                'n' => value.push('\n'),
                'r' => value.push('\r'),
                't' => value.push('\t'),
                other => value.push(other),
            }
            escaped = false;
            continue;
        }

        if character == '\\' {
            escaped = true;
            continue;
        }

        if character == '"' {
            return Some(value);
        }

        value.push(character);
    }

    None
}

fn development_backend_discovery_enabled() -> bool {
    cfg!(debug_assertions)
        || env::var(ALLOW_DEV_BACKEND_DISCOVERY_ENV_VAR)
            .map(|value| {
                matches!(
                    value.trim().to_ascii_lowercase().as_str(),
                    "1" | "true" | "yes"
                )
            })
            .unwrap_or(false)
}

fn canonical_backend_dir(path: &Path) -> Option<PathBuf> {
    let canonical = fs::canonicalize(path).ok()?;
    is_backend_dir(&canonical).then_some(canonical)
}

fn backend_beneath_resource_root(resource_root: &Path) -> Option<PathBuf> {
    let canonical_root = fs::canonicalize(resource_root).ok()?;
    let canonical_backend = canonical_backend_dir(&canonical_root.join("backend"))?;
    if canonical_backend.parent() != Some(canonical_root.as_path())
        || !canonical_backend.starts_with(&canonical_root)
    {
        return None;
    }
    Some(canonical_backend)
}

fn find_backend_dir_from_paths(
    resource_root: Option<&Path>,
    explicit_dev_backend: Option<&Path>,
    development_starts: &[PathBuf],
    allow_development_discovery: bool,
) -> Option<PathBuf> {
    if let Some(resource_root) = resource_root {
        if let Some(backend) = backend_beneath_resource_root(resource_root) {
            return Some(backend);
        }
    }

    if !allow_development_discovery {
        return None;
    }

    if let Some(explicit_backend) = explicit_dev_backend {
        if let Some(backend) = canonical_backend_dir(explicit_backend) {
            return Some(backend);
        }
    }

    for start in development_starts {
        for ancestor in start.ancestors() {
            if let Some(backend) = canonical_backend_dir(&ancestor.join("backend")) {
                return Some(backend);
            }
        }
    }
    None
}

fn find_backend_dir() -> Option<PathBuf> {
    let resource_root = env::var_os(RESOURCE_DIR_ENV_VAR).map(PathBuf::from);
    let explicit_dev_backend = env::var_os(DEV_BACKEND_DIR_ENV_VAR).map(PathBuf::from);
    let allow_development_discovery = development_backend_discovery_enabled();
    let mut development_starts = Vec::new();
    if allow_development_discovery {
        if let Ok(current_dir) = env::current_dir() {
            development_starts.push(current_dir);
        }
        if let Ok(current_exe) = env::current_exe() {
            if let Some(parent) = current_exe.parent() {
                development_starts.push(parent.to_path_buf());
            }
        }
    }
    find_backend_dir_from_paths(
        resource_root.as_deref(),
        explicit_dev_backend.as_deref(),
        &development_starts,
        allow_development_discovery,
    )
}

fn is_backend_dir(path: &Path) -> bool {
    path.is_dir()
        && path.join("dockstart_core").join("__init__.py").is_file()
        && path.join("dockstart_core").join("project.py").is_file()
        && path.join("dockstart_core").join("tool_check.py").is_file()
        && path.join("adapters").join("__init__.py").is_file()
}

fn fallback_check_error_json(message: &str, raw_error: &str) -> String {
    format!(
        "[{{\"key\":\"tool_check_backend\",\"name\":\"本机工具状态\",\"status\":\"error\",\"version\":\"\",\"path\":\"\",\"message\":\"{}\",\"raw_error\":\"{}\"}}]",
        json_escape(message),
        json_escape(raw_error)
    )
}

fn fallback_settings_error_json(message: &str, raw_error: &str) -> String {
    format!(
        "{{\"ok\":false,\"settings_path\":\"\",\"settings\":null,\"error\":{{\"message\":\"{}\",\"raw_error\":\"{}\"}}}}",
        json_escape(message),
        json_escape(raw_error)
    )
}

fn fallback_project_error_json(message: &str, raw_error: &str) -> String {
    format!(
        "{{\"ok\":false,\"project\":null,\"error\":{{\"code\":\"PYTHON_BACKEND_ERROR\",\"message\":\"{}\",\"raw_error\":\"{}\",\"suggestion\":\"请重新打开应用；若仍失败，请重新安装或恢复完整应用目录。\"}}}}",
        json_escape(message),
        json_escape(raw_error)
    )
}

fn fallback_toolchain_error_json(message: &str, raw_error: &str) -> String {
    format!(
        "{{\"ok\":false,\"runtime_mode\":\"unknown\",\"resource_dir\":\"\",\"toolchain_root\":\"\",\"tools_dir\":\"\",\"licenses_dir\":\"\",\"manifest_file\":\"\",\"manifest_exists\":false,\"manifest\":{{}},\"manifest_error\":\"\",\"bundled_vina\":{{\"exists\":false,\"path\":\"\",\"version\":\"\",\"status\":\"error\",\"message\":\"{}\",\"raw_error\":\"{}\"}},\"active_vina\":null,\"active_source\":\"unknown\",\"licenses\":{{\"exists\":false,\"third_party_notices\":\"\",\"third_party_notices_exists\":false}},\"resources\":{{\"exists\":false,\"tools_dir_exists\":false,\"vina_dir_exists\":false}},\"full_status\":\"missing\",\"message\":\"{}\",\"error\":{{\"code\":\"PYTHON_BACKEND_ERROR\",\"message\":\"{}\",\"raw_error\":\"{}\",\"suggestion\":\"请重新打开应用；若仍失败，请重新安装或恢复完整应用目录。\"}}}}",
        json_escape(message),
        json_escape(raw_error),
        json_escape(message),
        json_escape(message),
        json_escape(raw_error)
    )
}

fn json_escape(value: &str) -> String {
    let mut escaped = String::new();
    for character in value.chars() {
        match character {
            '\\' => escaped.push_str("\\\\"),
            '"' => escaped.push_str("\\\""),
            '\n' => escaped.push_str("\\n"),
            '\r' => escaped.push_str("\\r"),
            '\t' => escaped.push_str("\\t"),
            other => escaped.push(other),
        }
    }
    escaped
}

fn configure_resource_dir_env(app: &tauri::App) {
    #[cfg(not(debug_assertions))]
    {
        if let Ok(resource_dir) = app.path().resource_dir() {
            env::set_var(RESOURCE_DIR_ENV_VAR, resource_dir);
        }
        if let Ok(config_dir) = app.path().app_config_dir() {
            let _ = fs::create_dir_all(&config_dir);
            env::set_var(SETTINGS_ENV_VAR, config_dir.join("dockstart_settings.json"));
        }
    }

    #[cfg(debug_assertions)]
    {
        let _ = app;
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            configure_resource_dir_env(app);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_distribution_profile,
            refresh_runtime_cache,
            check_tools,
            get_toolchain_status,
            get_toolchain_repair_suggestions,
            run_post_install_check,
            export_diagnostic_report,
            get_app_capability_profile,
            get_project_mode_recommendation,
            get_minimum_requirements_status,
            list_available_demo_projects,
            create_demo_project,
            validate_demo_project,
            get_settings,
            save_settings,
            update_tool_path,
            diagnose_settings,
            create_project,
            load_project,
            import_receptor_pdbqt,
            import_ligand_pdbqt,
            search_rcsb_candidates,
            search_pubchem_candidates,
            preview_structure_candidate,
            fetch_pdb_structure,
            fetch_pubchem_ligand,
            get_raw_files_status,
            import_receptor_raw_file,
            import_ligand_raw_file,
            clear_receptor_raw_record,
            clear_ligand_raw_record,
            get_preparation_status,
            get_structure_review,
            validate_preparation_prerequisites,
            get_preparation_tool_status,
            get_flexible_receptor_status,
            get_flexible_receptor_identity_context,
            validate_flexible_receptor,
            set_receptor_docking_mode,
            get_macrocycle_status,
            review_ligand_macrocycle,
            confirm_ligand_macrocycle,
            reset_ligand_macrocycle_confirmation,
            get_result_export_status,
            start_preparation_task,
            start_flexible_receptor_task,
            start_result_export_task,
            start_pdb_fetch_task,
            start_pubchem_fetch_task,
            prepare_ligand_pdbqt,
            load_ligand_preparation_log,
            prepare_receptor_pdbqt,
            load_receptor_preparation_log,
            list_preparation_runs,
            load_preparation_metadata,
            get_latest_preparation,
            reset_preparation_status,
            get_box_params,
            update_box_params,
            get_vina_params,
            update_vina_params,
            update_run_settings,
            update_vina_run_protocol,
            get_vina_config_preview,
            generate_vina_config,
            get_autogrid_maps_defaults,
            get_autogrid_maps_status,
            set_scoring_protocol,
            get_ad4zn_status,
            save_ad4zn_review,
            prepare_ad4zn_receptor,
            set_ad4zn_parameter_file,
            generate_autogrid_maps,
            import_autogrid_maps,
            get_vina_maps_status,
            set_vina_maps_mode,
            generate_vina_maps,
            import_vina_maps,
            get_hydrated_status,
            prepare_hydrated_ligand,
            generate_hydrated_maps,
            get_hydrated_run_preflight,
            prepare_hydrated_run,
            load_hydrated_results,
            validate_run_prerequisites,
            get_run_preflight,
            get_project_run_guard,
            prepare_vina_run,
            prepare_multiple_ligand_run,
            get_project_workflow_status,
            load_run_metadata,
            start_vina_run_task,
            start_multiple_ligand_task,
            execute_prepared_vina_run,
            get_run_runtime_status,
            get_multiple_ligand_run_status,
            cancel_vina_run,
            cancel_multiple_ligand_run,
            get_background_task_status,
            find_active_background_task,
            cancel_background_task,
            get_run_files_status,
            analyze_vina_run_results,
            calculate_reference_ligand_rmsd,
            load_scores_csv,
            load_vina_evaluation,
            export_markdown_report,
            export_multiple_ligand_markdown_report,
            get_report_status,
            open_result_output_directory,
            read_project_markdown_report,
            get_viewer_file_status,
            load_structure_for_viewer,
            load_screening_candidate_for_viewer,
            list_docking_poses,
            load_docking_pose_for_viewer,
            load_multiple_ligand_pose,
            load_screening_pose_for_viewer,
            load_archived_screening_pose_for_viewer,
            load_local_only_pose_pair_for_viewer,
            load_pose_score_summary,
            get_box_visualization,
            update_box_from_visualization,
            stage_screening_inputs,
            create_screening,
            retry_screening_preparation,
            get_screening_status,
            list_screening_archives,
            get_screening_archive,
            export_screening_archive_zip,
            compare_screening_archives,
            request_screening_cancel,
            resume_screening,
            archive_screening,
            export_screening_report,
            generate_screening_result_sdf,
            start_screening_task
        ])
        .run(tauri::generate_context!())
        .expect("error while running DockStart");
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc, Barrier,
    };

    // The production cache is process-global, so cache tests must not
    // invalidate each other's generation while the Rust test harness runs
    // test functions in parallel.
    static BACKEND_CACHE_TEST_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn pdbqt_source_label_argument_is_safe_and_keeps_chemical_identity() {
        let normalized = normalize_pdbqt_source_label_arg("  InChI=1S/CH4/h1H4\0\n  配体 α  ");
        assert_eq!(normalized, "InChI=1S/CH4/h1H4 配体 α");
        assert_eq!(
            normalize_pdbqt_source_label_arg(&"配".repeat(300))
                .chars()
                .count(),
            240
        );
    }

    #[test]
    fn tauri_config_declares_the_main_workbench_window() {
        let context: tauri::Context<tauri::Wry> = tauri::generate_context!();
        let windows = &context.config().app.windows;

        assert_eq!(windows.len(), 1);
        assert_eq!(windows[0].label, "main");
        assert_eq!(windows[0].title, "DockStart");
    }

    #[test]
    fn distribution_profile_comes_from_the_packaged_manifest() {
        let test_root = env::temp_dir().join(format!(
            "dockstart-distribution-profile-{}-{}",
            std::process::id(),
            BACKGROUND_TASK_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&test_root).unwrap();
        let manifest = test_root.join("toolchain_manifest.json");

        fs::write(&manifest, br#"{"release_profile":"basic_stable"}"#).unwrap();
        let basic = distribution_profile_from_manifest(&manifest).unwrap();
        assert_eq!(basic["release_profile"], "basic_stable");
        assert_eq!(basic["display_name"], "Basic");
        assert_eq!(basic["message"], "当前安装为 DockStart Basic · 本地候选。");

        fs::write(&manifest, br#"{"release_profile":"assisted_stable"}"#).unwrap();
        let assisted = distribution_profile_from_manifest(&manifest).unwrap();
        assert_eq!(assisted["release_profile"], "assisted_stable");
        assert_eq!(assisted["display_name"], "Assisted");

        fs::write(&manifest, br#"{"release_profile":"custom"}"#).unwrap();
        assert!(distribution_profile_from_manifest(&manifest).is_err());
        let _ = fs::remove_dir_all(test_root);
    }

    fn create_test_backend(root: &Path) -> PathBuf {
        let backend = root.join("backend");
        fs::create_dir_all(backend.join("dockstart_core")).unwrap();
        fs::create_dir_all(backend.join("adapters")).unwrap();
        fs::write(backend.join("dockstart_core").join("__init__.py"), b"").unwrap();
        fs::write(backend.join("dockstart_core").join("project.py"), b"").unwrap();
        fs::write(backend.join("dockstart_core").join("tool_check.py"), b"").unwrap();
        fs::write(backend.join("adapters").join("__init__.py"), b"").unwrap();
        fs::canonicalize(backend).unwrap()
    }

    #[test]
    fn release_backend_discovery_does_not_fall_back_to_working_directory() {
        let test_root = env::temp_dir().join(format!(
            "dockstart-backend-trust-{}-{}",
            std::process::id(),
            BACKGROUND_TASK_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        let missing_resource_root = test_root.join("packaged-resources");
        let development_root = test_root.join("attacker-controlled");
        fs::create_dir_all(&missing_resource_root).unwrap();
        let development_backend = create_test_backend(&development_root);

        assert_eq!(
            find_backend_dir_from_paths(
                Some(&missing_resource_root),
                None,
                std::slice::from_ref(&development_root),
                false,
            ),
            None,
        );
        assert_eq!(
            find_backend_dir_from_paths(
                Some(&missing_resource_root),
                None,
                &[development_root],
                true,
            ),
            Some(development_backend),
        );
        let _ = fs::remove_dir_all(test_root);
    }

    #[test]
    fn packaged_backend_must_be_the_direct_canonical_resource_child() {
        let test_root = env::temp_dir().join(format!(
            "dockstart-packaged-backend-{}-{}",
            std::process::id(),
            BACKGROUND_TASK_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&test_root).unwrap();
        let expected = create_test_backend(&test_root);
        let unrelated_root = test_root.join("unrelated");
        let unrelated = create_test_backend(&unrelated_root);

        assert_eq!(
            find_backend_dir_from_paths(Some(&test_root), Some(&unrelated), &[], false),
            Some(expected),
        );
        assert_eq!(
            find_backend_dir_from_paths(None, Some(&unrelated), &[], false),
            None,
        );
        assert_eq!(
            find_backend_dir_from_paths(None, Some(&unrelated), &[], true),
            Some(unrelated),
        );
        let _ = fs::remove_dir_all(test_root);
    }

    #[test]
    fn preparation_tool_snapshot_requires_all_three_tools() {
        let ready = serde_json::json!({
            "python": {"status": "ok"},
            "rdkit": {"status": "ok"},
            "meeko": {"status": "ok"},
        });
        let transient_meeko_error = serde_json::json!({
            "python": {"status": "ok"},
            "rdkit": {"status": "ok"},
            "meeko": {"status": "error"},
        });

        assert!(preparation_tools_ready(&ready));
        assert!(!preparation_tools_ready(&transient_meeko_error));
        assert!(!preparation_tools_ready(&serde_json::Value::Null));
    }

    #[test]
    fn macrocycle_review_uses_the_resolved_preparation_python() {
        let configured = serde_json::json!({
            "python": {
                "status": "ok",
                "path": r"C:\tools\meeko\python.exe",
                "source": "configured"
            },
            "rdkit": {"status": "ok"},
            "meeko": {"status": "ok"},
        });
        let missing = serde_json::json!({
            "python": {
                "status": "missing",
                "path": r"C:\tools\missing\python.exe"
            }
        });

        assert_eq!(
            preparation_python_path(&configured).as_deref(),
            Some(r"C:\tools\meeko\python.exe")
        );
        assert_eq!(preparation_python_path(&missing), None);
        assert_eq!(preparation_python_path(&serde_json::Value::Null), None);
    }

    #[test]
    fn structure_fetch_task_key_tracks_the_selected_candidate() {
        let project = "project-key";
        let first = structure_fetch_task_key(project, "receptor", &["1IEP", "pdb", "false"]);
        let same = structure_fetch_task_key(project, "receptor", &["1IEP", "pdb", "false"]);
        let second = structure_fetch_task_key(project, "receptor", &["3PTB", "pdb", "false"]);
        let cif = structure_fetch_task_key(project, "receptor", &["1IEP", "cif", "false"]);

        assert_eq!(first, same);
        assert_ne!(first, second);
        assert_ne!(first, cif);
    }

    #[test]
    fn preparation_task_key_tracks_the_raw_input() {
        let test_root = env::temp_dir().join(format!(
            "dockstart-preparation-key-{}-{}",
            std::process::id(),
            BACKGROUND_TASK_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        let raw_dir = test_root.join("raw");
        fs::create_dir_all(&raw_dir).unwrap();
        fs::write(
            test_root.join("project.json"),
            br#"{"receptor":{"raw_file":"raw/receptor.pdb"}}"#,
        )
        .unwrap();
        fs::write(raw_dir.join("receptor.pdb"), b"ATOM A").unwrap();

        let first = preparation_task_key(&test_root.to_string_lossy(), "receptor");
        let same = preparation_task_key(&test_root.to_string_lossy(), "receptor");
        fs::write(
            raw_dir.join("receptor.pdb"),
            b"ATOM B with different length",
        )
        .unwrap();
        let changed = preparation_task_key(&test_root.to_string_lossy(), "receptor");

        assert_eq!(first, same);
        assert_ne!(first, changed);
        let _ = fs::remove_dir_all(test_root);
    }

    #[test]
    fn preparation_task_key_tracks_overwrite_and_protocol_options() {
        let project = "project-key";
        let standard = preparation_task_key_with_options(project, "ligand", false, None);
        let overwrite = preparation_task_key_with_options(project, "ligand", true, None);
        let macrocycle = preparation_task_key_with_options(
            project,
            "ligand",
            false,
            Some(r#"{"protocol":"meeko_macrocycle","macrocycle":{"mode":"auto"}}"#),
        );
        let same_macrocycle = preparation_task_key_with_options(
            project,
            "ligand",
            false,
            Some(r#"{"protocol":"meeko_macrocycle","macrocycle":{"mode":"auto"}}"#),
        );

        assert_ne!(standard, overwrite);
        assert_ne!(standard, macrocycle);
        assert_eq!(macrocycle, same_macrocycle);
    }

    #[test]
    fn macrocycle_commands_match_the_backend_cli_contract() {
        let project = r"C:\project with space".to_string();
        let options = r#"{"max_breaks":2,"allow_atom_type_a":true}"#.to_string();

        assert_eq!(MACROCYCLE_BACKEND_MODULE, "dockstart_core.macrocycle");
        assert_eq!(
            macrocycle_status_args(project.clone()),
            vec!["status", "--project", r"C:\project with space"]
        );
        assert_eq!(
            macrocycle_review_args(project.clone(), Some(options.clone())),
            vec![
                "review",
                "--project",
                r"C:\project with space",
                "--options-json",
                options.as_str(),
            ]
        );
        assert_eq!(
            macrocycle_review_args(project.clone(), Some("  ".to_string())),
            vec!["review", "--project", r"C:\project with space"]
        );
        assert_eq!(
            macrocycle_confirm_args(
                project.clone(),
                "macro_review_001".to_string(),
                Some("candidate_003".to_string()),
                false,
            )
            .unwrap(),
            vec![
                "confirm",
                "--project",
                r"C:\project with space",
                "--review-id",
                "macro_review_001",
                "--candidate-id",
                "candidate_003",
            ]
        );
        assert_eq!(
            macrocycle_confirm_args(project.clone(), "macro_review_001".to_string(), None, true,)
                .unwrap(),
            vec![
                "confirm",
                "--project",
                r"C:\project with space",
                "--review-id",
                "macro_review_001",
                "--rigid",
            ]
        );
        assert_eq!(
            macrocycle_reset_args(project),
            vec!["reset", "--project", r"C:\project with space"]
        );
    }

    #[test]
    fn macrocycle_confirmation_requires_exactly_one_selection_mode() {
        assert!(
            macrocycle_confirm_args("project".to_string(), "review".to_string(), None, false,)
                .is_err()
        );
        assert!(macrocycle_confirm_args(
            "project".to_string(),
            "review".to_string(),
            Some("candidate".to_string()),
            true,
        )
        .is_err());
    }

    #[test]
    fn backend_python_commands_disable_bytecode_writes() {
        let backend_dir = Path::new("backend-dir");
        let args = vec!["status".to_string(), "project with space".to_string()];
        let command = build_python_module_command(
            backend_dir,
            "bundled-python.exe",
            "dockstart_core.project",
            &args,
        );

        let command_args = command
            .get_args()
            .map(|value| value.to_string_lossy().into_owned())
            .collect::<Vec<_>>();
        assert_eq!(
            command_args,
            vec![
                "-B",
                "-m",
                "dockstart_core.project",
                "status",
                "project with space",
            ],
        );
        assert_eq!(command.get_current_dir(), Some(backend_dir));

        let environment = command
            .get_envs()
            .map(|(key, value)| {
                (
                    key.to_string_lossy().into_owned(),
                    value.map(|item| item.to_string_lossy().into_owned()),
                )
            })
            .collect::<std::collections::HashMap<_, _>>();
        assert_eq!(
            environment.get("PYTHONIOENCODING"),
            Some(&Some("utf-8".to_string())),
        );
        assert_eq!(
            environment.get("PYTHONDONTWRITEBYTECODE"),
            Some(&Some("1".to_string())),
        );
    }

    #[test]
    fn managed_process_test_child() {
        if env::var("DOCKSTART_TEST_MANAGED_PROCESS_CHILD").as_deref() == Ok("1") {
            std::thread::sleep(Duration::from_secs(30));
        }
    }

    #[test]
    fn managed_process_honors_running_cancellation() {
        let mut command = Command::new(env::current_exe().unwrap());
        command
            .arg("--exact")
            .arg("tests::managed_process_test_child")
            .arg("--nocapture")
            .env("DOCKSTART_TEST_MANAGED_PROCESS_CHILD", "1");
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        let polls = AtomicUsize::new(0);
        let started = Instant::now();
        let result = run_captured_command_managed(&mut command, Duration::from_secs(10), || {
            polls.fetch_add(1, Ordering::Relaxed) >= 2
        });

        assert_eq!(result.unwrap_err(), ManagedProcessError::Cancelled);
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn managed_process_timeouts_are_conservative_for_scientific_runs() {
        assert_eq!(
            background_job_timeout("structure-fetch"),
            Duration::from_secs(20 * 60)
        );
        assert_eq!(
            background_job_timeout("preparation"),
            Duration::from_secs(6 * 60 * 60)
        );
        assert_eq!(
            background_job_timeout("vina"),
            Duration::from_secs(48 * 60 * 60)
        );
        assert_eq!(
            backend_module_timeout("dockstart_core.project", &["execute-run".to_string()]),
            Duration::from_secs(48 * 60 * 60)
        );
    }

    #[test]
    fn preparation_python_command_receives_cached_tool_snapshot() {
        let command = build_python_module_command_with_env(
            Path::new("backend-dir"),
            "bundled-python.exe",
            "dockstart_core.preparation",
            &["prepare-receptor".to_string()],
            &[(PREPARATION_TOOLS_SNAPSHOT_ENV_VAR, "{\"python\":{}}")],
        );
        let environment = command
            .get_envs()
            .map(|(key, value)| {
                (
                    key.to_string_lossy().into_owned(),
                    value.map(|item| item.to_string_lossy().into_owned()),
                )
            })
            .collect::<std::collections::HashMap<_, _>>();
        assert_eq!(
            environment.get(PREPARATION_TOOLS_SNAPSHOT_ENV_VAR),
            Some(&Some("{\"python\":{}}".to_string())),
        );
    }

    #[test]
    fn cached_backend_reads_coalesce_concurrent_identical_requests() {
        let _test_guard = BACKEND_CACHE_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        invalidate_backend_read_cache();
        let calls = Arc::new(AtomicUsize::new(0));
        let barrier = Arc::new(Barrier::new(9));
        let unique_arg = format!("cache-test-{}", std::process::id());
        let mut workers = Vec::new();

        for _ in 0..8 {
            let calls = Arc::clone(&calls);
            let barrier = Arc::clone(&barrier);
            let unique_arg = unique_arg.clone();
            workers.push(std::thread::spawn(move || {
                barrier.wait();
                run_backend_module_cached_with(
                    "dockstart.cache.test",
                    vec![unique_arg],
                    Duration::from_secs(1),
                    move |_, _| {
                        calls.fetch_add(1, Ordering::SeqCst);
                        std::thread::sleep(Duration::from_millis(75));
                        Ok("payload".to_string())
                    },
                )
            }));
        }

        barrier.wait();
        for worker in workers {
            assert_eq!(
                worker.join().expect("cache worker panicked").unwrap(),
                "payload"
            );
        }
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn cache_invalidation_drops_an_inflight_stale_result() {
        let _test_guard = BACKEND_CACHE_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        invalidate_backend_read_cache();
        let entered = Arc::new(Barrier::new(2));
        let release = Arc::new(Barrier::new(2));
        let unique_arg = format!("generation-test-{}", std::process::id());
        let worker_arg = unique_arg.clone();
        let worker_entered = Arc::clone(&entered);
        let worker_release = Arc::clone(&release);

        let worker = std::thread::spawn(move || {
            run_backend_module_cached_with(
                "dockstart.cache.generation.test",
                vec![worker_arg],
                Duration::from_secs(1),
                move |_, _| {
                    worker_entered.wait();
                    worker_release.wait();
                    Ok("stale".to_string())
                },
            )
        });

        entered.wait();
        invalidate_backend_read_cache();
        release.wait();
        assert_eq!(
            worker.join().expect("cache worker panicked").unwrap(),
            "stale"
        );

        let refreshed = run_backend_module_cached_with(
            "dockstart.cache.generation.test",
            vec![unique_arg],
            Duration::from_secs(1),
            |_, _| Ok("fresh".to_string()),
        )
        .unwrap();
        assert_eq!(refreshed, "fresh");
    }

    #[test]
    fn project_mutation_does_not_flush_runtime_probe_cache() {
        let _test_guard = BACKEND_CACHE_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        invalidate_backend_read_cache();
        let calls = Arc::new(AtomicUsize::new(0));

        let first_calls = Arc::clone(&calls);
        let first = run_backend_module_cached_with(
            "dockstart_core.toolchain",
            Vec::new(),
            Duration::from_secs(60),
            move |_, _| {
                first_calls.fetch_add(1, Ordering::SeqCst);
                Ok("runtime-a".to_string())
            },
        )
        .unwrap();
        assert_eq!(first, "runtime-a");

        invalidate_backend_cache(CacheInvalidation::Project);
        let second_calls = Arc::clone(&calls);
        let second = run_backend_module_cached_with(
            "dockstart_core.toolchain",
            Vec::new(),
            Duration::from_secs(60),
            move |_, _| {
                second_calls.fetch_add(1, Ordering::SeqCst);
                Ok("runtime-b".to_string())
            },
        )
        .unwrap();
        assert_eq!(second, "runtime-a");
        assert_eq!(calls.load(Ordering::SeqCst), 1);

        invalidate_backend_cache(CacheInvalidation::Runtime);
        let third_calls = Arc::clone(&calls);
        let third = run_backend_module_cached_with(
            "dockstart_core.toolchain",
            Vec::new(),
            Duration::from_secs(60),
            move |_, _| {
                third_calls.fetch_add(1, Ordering::SeqCst);
                Ok("runtime-c".to_string())
            },
        )
        .unwrap();
        assert_eq!(third, "runtime-c");
        assert_eq!(calls.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn viewer_pose_fingerprint_tracks_output_artifact_changes() {
        let test_root = env::temp_dir().join(format!(
            "dockstart-viewer-cache-{}-{}",
            std::process::id(),
            BACKGROUND_TASK_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        let run_dir = test_root.join("runs").join("run_001");
        fs::create_dir_all(&run_dir).unwrap();
        fs::write(test_root.join("project.json"), b"{}").unwrap();
        fs::write(run_dir.join("metadata.json"), b"{\"status\":\"finished\"}").unwrap();
        fs::write(run_dir.join("out.pdbqt"), b"A").unwrap();
        let args = vec![
            "load-pose".to_string(),
            test_root.to_string_lossy().into_owned(),
            "run_001".to_string(),
            "1".to_string(),
        ];
        let before = viewer_artifact_fingerprint(&args);
        fs::write(run_dir.join("out.pdbqt"), b"AB").unwrap();
        let after = viewer_artifact_fingerprint(&args);
        assert_ne!(before, after);

        fs::write(run_dir.join("optimized.pdbqt"), b"A").unwrap();
        let optimized_before = viewer_artifact_fingerprint(&args);
        fs::write(run_dir.join("optimized.pdbqt"), b"AB").unwrap();
        let optimized_after = viewer_artifact_fingerprint(&args);
        assert_ne!(optimized_before, optimized_after);

        let inputs_dir = run_dir.join("inputs");
        fs::create_dir_all(&inputs_dir).unwrap();
        fs::write(inputs_dir.join("ligand.pdbqt"), b"A").unwrap();
        let score_only_before = viewer_artifact_fingerprint(&args);
        fs::write(inputs_dir.join("ligand.pdbqt"), b"AB").unwrap();
        let score_only_after = viewer_artifact_fingerprint(&args);
        assert_ne!(score_only_before, score_only_after);

        fs::write(inputs_dir.join("receptor.pdbqt"), b"A").unwrap();
        let pair_args = vec![
            "load-local-pose-pair".to_string(),
            test_root.to_string_lossy().into_owned(),
            "run_001".to_string(),
        ];
        let pair_before = viewer_artifact_fingerprint(&pair_args);
        fs::write(inputs_dir.join("receptor.pdbqt"), b"AB").unwrap();
        let receptor_after = viewer_artifact_fingerprint(&pair_args);
        assert_ne!(pair_before, receptor_after);
        fs::write(inputs_dir.join("ligand.pdbqt"), b"ABC").unwrap();
        let ligand_after = viewer_artifact_fingerprint(&pair_args);
        assert_ne!(receptor_after, ligand_after);
        fs::write(run_dir.join("optimized.pdbqt"), b"ABC").unwrap();
        let pair_optimized_after = viewer_artifact_fingerprint(&pair_args);
        assert_ne!(ligand_after, pair_optimized_after);
        fs::write(inputs_dir.join("flex.pdbqt"), b"A").unwrap();
        let pair_flex_after = viewer_artifact_fingerprint(&pair_args);
        assert_ne!(pair_optimized_after, pair_flex_after);
        let _ = fs::remove_dir_all(test_root);
    }

    #[test]
    fn viewer_screening_pose_fingerprint_tracks_scoped_artifacts() {
        assert!(is_safe_screening_item_id("ligand_0001"));
        assert!(is_safe_screening_item_id("ligand_10000"));
        for invalid in [
            "",
            "ligand_001",
            "ligand_abcd",
            "../ligand_0001",
            "ligand_0001/child",
            "ligand_0001\\child",
        ] {
            assert!(
                !is_safe_screening_item_id(invalid),
                "unexpectedly accepted {invalid}"
            );
        }

        let test_root = env::temp_dir().join(format!(
            "dockstart-screening-viewer-cache-{}-{}",
            std::process::id(),
            BACKGROUND_TASK_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        let screening_dir = test_root.join("screening");
        let attempt_dir = screening_dir
            .join("attempts")
            .join("ligand_0001")
            .join("attempt_001");
        fs::create_dir_all(screening_dir.join("inputs")).unwrap();
        fs::create_dir_all(&attempt_dir).unwrap();
        fs::write(test_root.join("project.json"), b"{}").unwrap();
        fs::write(
            screening_dir.join("screening.json"),
            b"{\"status\":\"completed\"}",
        )
        .unwrap();
        fs::write(screening_dir.join("inputs").join("receptor.pdbqt"), b"A").unwrap();
        fs::write(attempt_dir.join("out.pdbqt"), b"A").unwrap();

        let args = vec![
            "load-screening-pose".to_string(),
            test_root.to_string_lossy().into_owned(),
            "ligand_0001".to_string(),
            "1".to_string(),
        ];
        let before = viewer_artifact_fingerprint(&args);
        fs::write(
            screening_dir.join("screening.json"),
            b"{\"status\":\"completed\",\"reported\":true}",
        )
        .unwrap();
        let state_after = viewer_artifact_fingerprint(&args);
        assert_ne!(before, state_after);

        fs::write(screening_dir.join("inputs").join("receptor.pdbqt"), b"AB").unwrap();
        let receptor_after = viewer_artifact_fingerprint(&args);
        assert_ne!(state_after, receptor_after);

        fs::write(attempt_dir.join("out.pdbqt"), b"ABC").unwrap();
        let output_after = viewer_artifact_fingerprint(&args);
        assert_ne!(receptor_after, output_after);

        let unsafe_args = vec![
            "load-screening-pose".to_string(),
            test_root.to_string_lossy().into_owned(),
            "../ligand_0001".to_string(),
            "1".to_string(),
        ];
        let unsafe_before = viewer_artifact_fingerprint(&unsafe_args);
        fs::write(attempt_dir.join("out.pdbqt"), b"ABCD").unwrap();
        let unsafe_after = viewer_artifact_fingerprint(&unsafe_args);
        assert_eq!(unsafe_before, unsafe_after);

        let _ = fs::remove_dir_all(test_root);
    }

    #[test]
    fn screening_archive_argument_validation_is_strict() {
        assert!(is_safe_screening_candidate_id("ligand_012345abcdef_0001"));
        assert!(is_safe_screening_candidate_id(
            "ligand_012345abcdef_0001_02"
        ));
        assert!(!is_safe_screening_candidate_id(
            "ligand_012345abcdef_0001_extra"
        ));
        assert!(!is_safe_screening_candidate_id(
            "../ligand_012345abcdef_0001"
        ));
        assert!(is_sha256_hex(&"a".repeat(64)));
        assert!(!is_sha256_hex(&"g".repeat(64)));
        assert!(!is_sha256_hex(&"a".repeat(63)));

        for valid in [
            "screening_001_20260728123456",
            "screening_1000_20260728123456",
            "screening_001_20260728123456_01",
            "screening_001_20260728123456_100",
        ] {
            assert!(
                is_safe_screening_archive_id(valid),
                "unexpectedly rejected {valid}"
            );
        }
        for invalid in [
            "",
            "screening_01_20260728123456",
            "screening_001_2026072812345",
            "screening_001_202607281234567",
            "screening_001_20260728123456_1",
            "screening_001_20260728123456_extra",
            "screening_001_20260728123456_01_extra",
            "../screening_001_20260728123456",
            "screening_001/20260728123456",
            "screening_001\\20260728123456",
            "screening_００１_20260728123456",
        ] {
            assert!(
                !is_safe_screening_archive_id(invalid),
                "unexpectedly accepted {invalid}"
            );
        }

        assert!(is_safe_screening_pose_mode(None));
        assert!(is_safe_screening_pose_mode(Some(1)));
        assert!(is_safe_screening_pose_mode(Some(50)));
        assert!(!is_safe_screening_pose_mode(Some(-1)));
        assert!(!is_safe_screening_pose_mode(Some(0)));
        assert!(!is_safe_screening_pose_mode(Some(51)));

        let baseline = "screening_001_20260728123456".to_string();
        let comparison = "screening_002_20260728123556".to_string();
        assert!(are_safe_screening_archive_comparison_ids(&[
            baseline.clone(),
            comparison,
        ]));
        assert!(!are_safe_screening_archive_comparison_ids(
            std::slice::from_ref(&baseline)
        ));
        assert!(!are_safe_screening_archive_comparison_ids(&[
            baseline.clone(),
            baseline,
        ]));
        assert!(!are_safe_screening_archive_comparison_ids(&[
            "screening_001_20260728123456".to_string(),
            "../screening_002_20260728123556".to_string(),
        ]));
    }

    #[test]
    fn flexible_identity_arguments_are_sorted_and_hash_bound() {
        let mut args = vec!["prepare".to_string()];
        let choices = HashMap::from([
            ("B:2".to_string(), "A".to_string()),
            ("A:10:A".to_string(), "B".to_string()),
        ]);

        append_flexible_identity_args(&mut args, Some(choices), Some("A".repeat(64))).unwrap();

        assert_eq!(
            args,
            vec![
                "prepare",
                "--resolved-altloc",
                "A:10:A=B",
                "--resolved-altloc",
                "B:2=A",
                "--expected-selection-context-sha256",
                &"a".repeat(64),
            ]
        );
    }

    #[test]
    fn flexible_identity_arguments_reject_invalid_altloc_or_hash() {
        let mut args = Vec::new();
        assert!(append_flexible_identity_args(
            &mut args,
            Some(HashMap::from([("A:10".to_string(), "AB".to_string())])),
            None,
        )
        .is_err());
        assert!(
            append_flexible_identity_args(&mut args, None, Some("not-a-sha256".to_string()),)
                .is_err()
        );
    }

    #[test]
    fn screening_archive_export_args_preserve_values_and_reject_overwrite() {
        let expected_without_overwrite = vec![
            "archive-export".to_string(),
            "--project".to_string(),
            r"C:\projects\screening demo".to_string(),
            "--archive-id".to_string(),
            "screening_001_20260728123456".to_string(),
            "--output".to_string(),
            r"D:\exports\screening result.zip".to_string(),
        ];

        assert_eq!(
            build_screening_archive_export_args(
                r"C:\projects\screening demo",
                "screening_001_20260728123456",
                r"D:\exports\screening result.zip",
                None,
            ),
            Ok(expected_without_overwrite.clone())
        );
        assert_eq!(
            build_screening_archive_export_args(
                r"C:\projects\screening demo",
                "screening_001_20260728123456",
                r"D:\exports\screening result.zip",
                Some(false),
            ),
            Ok(expected_without_overwrite.clone())
        );
        assert_eq!(
            build_screening_archive_export_args(
                r"C:\projects\screening demo",
                "screening_001_20260728123456",
                r"D:\exports\screening result.zip",
                Some(true),
            ),
            Err(ScreeningArchiveExportArgumentError::OverwriteUnsupported)
        );
        assert!(!expected_without_overwrite.iter().any(|value| {
            matches!(
                value.as_str(),
                "--overwrite"
                    | "--expected-destination-sha256"
                    | "--expected-destination-size-bytes"
            )
        }));

        let error_payload = screening_archive_export_overwrite_error_json();
        let error_value: serde_json::Value = serde_json::from_str(&error_payload).unwrap();
        assert_eq!(
            error_value
                .pointer("/error/code")
                .and_then(serde_json::Value::as_str),
            Some("SCREENING_ARCHIVE_EXPORT_OVERWRITE_UNSUPPORTED")
        );
    }

    #[test]
    fn screening_archive_export_output_only_preserves_structured_business_errors() {
        let business_error = b" \n{\"ok\":false,\"error\":{\"code\":\"SCREENING_ARCHIVE_EXPORT_DESTINATION_EXISTS\",\"details\":{\"destination_file\":\"D:\\\\exports\\\\screening.zip\",\"destination_sha256\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"destination_size_bytes\":12345}}}\r\n";
        assert_eq!(
            classify_screening_archive_export_output(false, business_error, b""),
            Ok(String::from_utf8(business_error.to_vec()).unwrap())
        );
        let preserved: serde_json::Value = serde_json::from_slice(business_error).unwrap();
        assert_eq!(
            preserved
                .pointer("/error/details/destination_size_bytes")
                .and_then(serde_json::Value::as_u64),
            Some(12345)
        );

        for rejected in [
            b"{\"ok\":true,\"message\":\"unexpected exit\"}".as_slice(),
            b"{\"error\":{\"code\":\"ARCHIVE_EXISTS\"}}".as_slice(),
            b"backend import failed".as_slice(),
            b"{\"ok\":false}\n{\"ok\":false}".as_slice(),
        ] {
            assert!(
                classify_screening_archive_export_output(false, rejected, b"traceback").is_err(),
                "unexpectedly preserved {}",
                String::from_utf8_lossy(rejected)
            );
        }
    }

    #[test]
    fn background_backend_output_preserves_structured_review_errors() {
        let review = b"{\"ok\":false,\"error\":{\"code\":\"FLEX_BAD_RESIDUES_REVIEW_REQUIRED\"},\"review\":{\"bad_residues\":[\"A:226\",\"A:229\"]}}";
        assert_eq!(
            classify_structured_backend_output(false, review, b"meeko diagnostic"),
            Ok(String::from_utf8(review.to_vec()).unwrap())
        );

        let malformed = b"{\"ok\":false";
        let error = classify_structured_backend_output(false, malformed, b"traceback").unwrap_err();
        assert!(error.contains("traceback"));
    }

    #[test]
    fn screening_archive_export_runner_continues_after_non_business_failures() {
        let business_error = "{\"ok\":false,\"error\":{\"code\":\"ARCHIVE_EXISTS\"}}";
        let mut attempted = Vec::new();
        let result = run_screening_archive_export_candidates(
            vec![
                "missing-python".to_string(),
                "wrong-runtime".to_string(),
                "working-runtime".to_string(),
            ],
            |python| {
                attempted.push(python.to_string());
                match python {
                    "missing-python" => Err("interpreter unavailable".to_string()),
                    "wrong-runtime" => classify_screening_archive_export_output(
                        false,
                        b"{\"ok\":true}",
                        b"module import failed",
                    ),
                    _ => classify_screening_archive_export_output(
                        false,
                        business_error.as_bytes(),
                        b"",
                    ),
                }
            },
        );

        assert_eq!(result, Ok(business_error.to_string()));
        assert_eq!(
            attempted,
            vec![
                "missing-python".to_string(),
                "wrong-runtime".to_string(),
                "working-runtime".to_string(),
            ]
        );

        let failure = run_screening_archive_export_candidates(
            vec!["python-a".to_string(), "python-b".to_string()],
            |python| Err(format!("{python} unavailable")),
        )
        .unwrap_err();
        assert!(failure.contains("python-a: python-a unavailable"));
        assert!(failure.contains("python-b: python-b unavailable"));
    }

    #[test]
    fn backend_mutation_scope_is_narrow() {
        assert_eq!(
            backend_command_invalidation(
                "dockstart_core.project",
                &["workflow-status".to_string(), "project".to_string()],
            ),
            CacheInvalidation::None
        );
        assert_eq!(
            backend_command_invalidation(
                "dockstart_core.project",
                &["update-box".to_string(), "project".to_string()],
            ),
            CacheInvalidation::Project
        );
        assert_eq!(
            backend_command_invalidation(
                "dockstart_core.project",
                &[
                    "update-run-protocol".to_string(),
                    "project".to_string(),
                    "score_only".to_string(),
                    "true".to_string(),
                ],
            ),
            CacheInvalidation::Project
        );
        assert_eq!(
            backend_command_invalidation(
                "dockstart_core.settings",
                &["update-tool-path".to_string()],
            ),
            CacheInvalidation::Both
        );
        assert_eq!(
            backend_command_invalidation(
                "dockstart_core.macrocycle",
                &[
                    "status".to_string(),
                    "--project".to_string(),
                    "project".to_string(),
                ],
            ),
            CacheInvalidation::None
        );
        for command in ["review", "confirm", "reset"] {
            assert_eq!(
                backend_command_invalidation(
                    "dockstart_core.macrocycle",
                    &[
                        command.to_string(),
                        "--project".to_string(),
                        "project".to_string(),
                    ],
                ),
                CacheInvalidation::Project
            );
        }
        assert_eq!(
            backend_command_invalidation(
                "dockstart_core.screening",
                &[
                    "archive".to_string(),
                    "--project".to_string(),
                    "project".to_string(),
                ],
            ),
            CacheInvalidation::Project
        );
        assert_eq!(
            backend_command_invalidation(
                "dockstart_core.screening",
                &[
                    "archives".to_string(),
                    "--project".to_string(),
                    "project".to_string(),
                ],
            ),
            CacheInvalidation::None
        );
        assert_eq!(
            backend_command_invalidation(
                "dockstart_core.screening",
                &[
                    "archive-export".to_string(),
                    "--project".to_string(),
                    "project".to_string(),
                    "--archive-id".to_string(),
                    "screening_001_20260728123456".to_string(),
                    "--output".to_string(),
                    "screening.zip".to_string(),
                ],
            ),
            CacheInvalidation::None
        );
        assert_eq!(
            backend_command_invalidation(
                "dockstart_core.vina_maps",
                &["status".to_string(), "project".to_string()],
            ),
            CacheInvalidation::None
        );
        for command in ["set-mode", "generate", "import"] {
            assert_eq!(
                backend_command_invalidation(
                    "dockstart_core.vina_maps",
                    &[command.to_string(), "project".to_string()],
                ),
                CacheInvalidation::Project
            );
        }
        for command in ["prepare", "run", "cancel", "report"] {
            assert_eq!(
                backend_command_invalidation(
                    "dockstart_core.multiple_ligands",
                    &[command.to_string(), "project".to_string()],
                ),
                CacheInvalidation::Project
            );
        }
        for command in ["status", "load-pose"] {
            assert_eq!(
                backend_command_invalidation(
                    "dockstart_core.multiple_ligands",
                    &[command.to_string(), "project".to_string()],
                ),
                CacheInvalidation::None
            );
        }
        for command in ["prepare-ligand", "generate-maps"] {
            assert_eq!(
                backend_command_invalidation(
                    "dockstart_core.hydrated",
                    &[command.to_string(), "project".to_string()],
                ),
                CacheInvalidation::Project
            );
        }
        assert_eq!(
            backend_command_invalidation(
                "dockstart_core.hydrated",
                &["status".to_string(), "project".to_string()],
            ),
            CacheInvalidation::None
        );
        assert_eq!(
            backend_command_invalidation(
                "dockstart_core.hydrated_run",
                &["prepare".to_string(), "project".to_string()],
            ),
            CacheInvalidation::Project
        );
        for command in ["preflight", "results"] {
            assert_eq!(
                backend_command_invalidation(
                    "dockstart_core.hydrated_run",
                    &[command.to_string(), "project".to_string()],
                ),
                CacheInvalidation::None
            );
        }
    }

    #[test]
    fn hydrated_generate_maps_args_keep_options_as_one_json_argument() {
        let options_json = r#"{"spacing":0.375,"grid_points":{"x":48,"y":50,"z":52}}"#.to_string();
        assert_eq!(
            hydrated_generate_maps_args("C:\\project path".to_string(), Some(options_json.clone()),),
            vec![
                "generate-maps".to_string(),
                "C:\\project path".to_string(),
                "--options-json".to_string(),
                options_json,
            ]
        );
        assert_eq!(
            hydrated_generate_maps_args("C:\\project path".to_string(), None),
            vec!["generate-maps".to_string(), "C:\\project path".to_string(),]
        );
        assert_eq!(
            hydrated_generate_maps_args("C:\\project path".to_string(), Some("   ".to_string()),),
            vec!["generate-maps".to_string(), "C:\\project path".to_string(),]
        );
    }

    #[test]
    fn multiple_ligand_prepare_uses_one_json_argument_and_preserves_member_order() {
        let args = multiple_ligand_prepare_args(
            "C:\\project path".to_string(),
            vec![
                "C:\\ligands\\first ligand.pdbqt".to_string(),
                "D:\\second.pdbqt".to_string(),
            ],
        )
        .unwrap();
        assert_eq!(args[0], "prepare");
        assert_eq!(args[1], "C:\\project path");
        let members: Vec<String> = serde_json::from_str(&args[2]).unwrap();
        assert_eq!(
            members,
            vec!["C:\\ligands\\first ligand.pdbqt", "D:\\second.pdbqt"]
        );
    }

    #[test]
    fn run_directory_validation_rejects_traversal_and_accepts_scoped_run() {
        assert!(is_safe_run_id("run_001"));
        assert!(is_safe_run_id("run_0001"));
        for invalid in [
            "",
            "run_01",
            "run_abc",
            "../run_001",
            "run_001/child",
            "run_001\\child",
        ] {
            assert!(!is_safe_run_id(invalid), "unexpectedly accepted {invalid}");
        }

        let test_root = env::temp_dir().join(format!(
            "dockstart-run-validation-{}-{}",
            std::process::id(),
            BACKGROUND_TASK_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        let run_dir = test_root.join("runs").join("run_001");
        fs::create_dir_all(&run_dir).unwrap();
        let validated = validate_run_directory(&test_root.to_string_lossy(), "run_001").unwrap();
        assert_eq!(validated, fs::canonicalize(&run_dir).unwrap());
        assert!(validate_run_directory(&test_root.to_string_lossy(), "../run_001").is_err());
        let _ = fs::remove_dir_all(test_root);
    }

    #[test]
    fn local_report_and_result_directories_stay_inside_the_project() {
        let test_root = env::temp_dir().join(format!(
            "dockstart-local-directory-{}-{}",
            std::process::id(),
            BACKGROUND_TASK_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        let reports_dir = test_root.join("reports");
        let export_dir = test_root
            .join("runs")
            .join("run_001")
            .join("exports")
            .join("sdf_001");
        fs::create_dir_all(&reports_dir).unwrap();
        fs::create_dir_all(&export_dir).unwrap();
        fs::write(test_root.join("project.json"), b"{}").unwrap();
        fs::write(reports_dir.join("docking_report.md"), b"# report\n").unwrap();
        fs::write(export_dir.join("poses.sdf"), b"$$$$\n").unwrap();

        let project_text = test_root.to_string_lossy();
        let reports =
            resolve_result_output_directory(&project_text, "reports", None, None).unwrap();
        assert_eq!(reports, fs::canonicalize(&reports_dir).unwrap());

        let sdf_directory = resolve_result_output_directory(
            &project_text,
            "result_sdf",
            Some("run_001"),
            Some("runs/run_001/exports/sdf_001/poses.sdf"),
        )
        .unwrap();
        assert_eq!(sdf_directory, fs::canonicalize(&export_dir).unwrap());

        let report =
            resolve_project_markdown_report(&project_text, "reports/docking_report.md").unwrap();
        assert_eq!(
            report,
            fs::canonicalize(reports_dir.join("docking_report.md")).unwrap()
        );
        assert!(resolve_project_markdown_report(&project_text, "../outside.md").is_err());
        assert!(resolve_result_output_directory(
            &project_text,
            "result_sdf",
            Some("run_001"),
            Some("runs/run_001/metadata.json"),
        )
        .is_err());
        assert!(resolve_result_output_directory(
            &project_text,
            "result_sdf",
            Some("../run_001"),
            Some("runs/run_001/exports/sdf_001/poses.sdf"),
        )
        .is_err());

        let _ = fs::remove_dir_all(test_root);
    }

    #[test]
    fn background_task_json_round_trips_control_characters() {
        let record = BackgroundTaskRecord {
            task_id: "task-control".to_string(),
            kind: "vina".to_string(),
            key: "key".to_string(),
            project_dir: "C:\\project\\line\nnext".to_string(),
            project_key: "project".to_string(),
            run_id: "run_001".to_string(),
            target: String::new(),
            status: "failed".to_string(),
            stage: "failed".to_string(),
            message: "quote \" slash \\ newline\nnull \u{0000}".to_string(),
            progress_percent: 42,
            progress_message: "tab\treturn\r".to_string(),
            stdout_tail: "stdout\u{001f}".to_string(),
            stderr_tail: "stderr\nline".to_string(),
            log_tail: "log".to_string(),
            created_at: Instant::now(),
            started_at: Some(Instant::now()),
            finished_at: Some(Instant::now()),
            result_json: "{\"ok\":false,\"message\":\"line\\nnext\"}".to_string(),
            error: "bad\u{0007}".to_string(),
            cancel_requested: false,
        };
        let serialized = background_task_status_json(&record, false);
        let parsed: serde_json::Value = serde_json::from_str(&serialized).unwrap();
        assert_eq!(parsed["message"], record.message);
        assert_eq!(parsed["result_json"], record.result_json);
        assert_eq!(parsed["error"], record.error);
        assert_eq!(parsed["progress"]["percent"], 42);
    }

    #[test]
    fn cancellation_payload_and_stage_are_monotonic_for_long_tasks() {
        assert!(payload_reports_cancelled(
            r#"{"ok":true,"metadata":{"status":"cancelled","stage":"cancelled"}}"#
        ));
        assert!(payload_reports_cancelled(
            r#"{"ok":true,"stage":"cancelled"}"#
        ));
        assert!(payload_reports_cancelled(
            r#"{"ok":true,"screening":{"status":"canceled"}}"#
        ));
        assert!(!payload_reports_cancelled(
            r#"{"ok":true,"metadata":{"status":"finished"}}"#
        ));
        assert_eq!(
            monotonic_vina_task_stage("running", "running", "cancelling"),
            "cancelling"
        );
        assert_eq!(
            monotonic_vina_task_stage("cancelling", "running", "running"),
            "cancelling"
        );
        assert_eq!(
            monotonic_vina_task_stage("cancelled", "running", "running"),
            "cancelled"
        );
    }

    #[test]
    fn project_cache_fingerprint_tracks_external_project_json_writes() {
        let test_root = env::temp_dir().join(format!(
            "dockstart-project-cache-{}-{}",
            std::process::id(),
            BACKGROUND_TASK_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&test_root).unwrap();
        fs::write(test_root.join("project.json"), b"{}").unwrap();
        let args = vec![
            "get-box".to_string(),
            test_root.to_string_lossy().into_owned(),
        ];
        let before = project_artifact_fingerprint("dockstart_core.project", &args);
        fs::write(test_root.join("project.json"), b"{\"revision\":2}").unwrap();
        let after = project_artifact_fingerprint("dockstart_core.project", &args);
        assert_ne!(before, after);
        let _ = fs::remove_dir_all(test_root);
    }

    #[test]
    fn recovery_reads_always_bypass_the_read_cache() {
        let _test_guard = BACKEND_CACHE_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        invalidate_backend_read_cache();
        let calls = Arc::new(AtomicUsize::new(0));
        for expected in 1..=2 {
            let calls_for_runner = Arc::clone(&calls);
            let payload = run_backend_module_cached_with(
                "dockstart_core.project",
                vec!["run-preflight".to_string(), "project".to_string()],
                Duration::from_secs(60),
                move |_, _| {
                    let call = calls_for_runner.fetch_add(1, Ordering::SeqCst) + 1;
                    Ok(format!("payload-{call}"))
                },
            )
            .unwrap();
            assert_eq!(payload, format!("payload-{expected}"));
        }
        assert_eq!(calls.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn cache_pruning_enforces_entry_and_byte_limits() {
        let now = Instant::now();
        let mut state = BackendReadCache::default();
        for index in 0..6 {
            state.ready.insert(
                format!("entry-{index}"),
                CachedBackendResult {
                    stored_at: now,
                    last_accessed: now + Duration::from_millis(index),
                    size_bytes: 8,
                    result: Ok("12345678".to_string()),
                    sensitivity: CacheSensitivity::default(),
                },
            );
        }
        prune_backend_cache_with_limits(&mut state, 4, 24, 10);
        assert!(state.ready.len() <= 4);
        assert!(
            state
                .ready
                .values()
                .map(|entry| entry.size_bytes)
                .sum::<usize>()
                <= 24
        );

        state.ready.insert(
            "oversize".to_string(),
            CachedBackendResult {
                stored_at: now,
                last_accessed: now,
                size_bytes: 11,
                result: Ok("12345678901".to_string()),
                sensitivity: CacheSensitivity::default(),
            },
        );
        prune_backend_cache_with_limits(&mut state, 4, 24, 10);
        assert!(!state.ready.contains_key("oversize"));
    }

    #[test]
    fn preparation_tool_snapshot_is_runtime_only_across_projects() {
        let _test_guard = BACKEND_CACHE_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        invalidate_backend_read_cache();
        let pseudo = cache_sensitivity("dockstart.runtime.preparation-tools", &[]);
        assert!(pseudo.runtime);
        assert!(!pseudo.project);
        let direct = cache_sensitivity(
            "dockstart_core.preparation",
            &["tool-status".to_string(), "project-a".to_string()],
        );
        assert!(direct.runtime);
        assert!(!direct.project);
        let calls = Arc::new(AtomicUsize::new(0));
        let first_calls = Arc::clone(&calls);
        let first = run_backend_module_cached_with(
            "dockstart.runtime.preparation-tools",
            Vec::new(),
            Duration::from_secs(60),
            move |_, _| {
                first_calls.fetch_add(1, Ordering::SeqCst);
                Ok("tools-a".to_string())
            },
        )
        .unwrap();
        invalidate_backend_cache(CacheInvalidation::Project);
        let second_calls = Arc::clone(&calls);
        let second = run_backend_module_cached_with(
            "dockstart.runtime.preparation-tools",
            Vec::new(),
            Duration::from_secs(60),
            move |_, _| {
                second_calls.fetch_add(1, Ordering::SeqCst);
                Ok("tools-b".to_string())
            },
        )
        .unwrap();
        assert_eq!(first, "tools-a");
        assert_eq!(second, "tools-a");
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }
}
