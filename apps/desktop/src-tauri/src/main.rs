#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    collections::{HashMap, HashSet},
    env, fs,
    path::{Path, PathBuf},
    process::Command,
    sync::{Condvar, Mutex, OnceLock},
    time::{Duration, Instant},
};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(not(debug_assertions))]
use tauri::Manager;

const RESOURCE_DIR_ENV_VAR: &str = "DOCKSTART_RESOURCE_DIR";
const SETTINGS_ENV_VAR: &str = "DOCKSTART_SETTINGS_PATH";

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

#[tauri::command]
async fn check_tools() -> String {
    match run_backend_module_cached_async(
        "dockstart_core.tool_check",
        Vec::new(),
        Duration::from_secs(2),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_check_error_json("无法调用 Python 后端工具检测入口。", &error),
    }
}

#[tauri::command]
async fn get_toolchain_status() -> String {
    match run_backend_module_cached_async(
        "dockstart_core.toolchain",
        Vec::new(),
        Duration::from_secs(2),
    )
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
        Duration::from_secs(1),
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
async fn import_receptor_pdbqt(project_dir: String, source_path: String) -> String {
    match run_backend_module_async(
        "dockstart_core.project",
        vec!["import-receptor".to_string(), project_dir, source_path],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导入受体 PDBQT。", &error),
    }
}

#[tauri::command]
async fn import_ligand_pdbqt(project_dir: String, source_path: String) -> String {
    match run_backend_module_async(
        "dockstart_core.project",
        vec!["import-ligand".to_string(), project_dir, source_path],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导入配体 PDBQT。", &error),
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
    match run_backend_module_cached_async(
        "dockstart_core.preparation",
        vec!["status".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 PDBQT 自动准备状态。", &error),
    }
}

#[tauri::command]
async fn validate_preparation_prerequisites(project_dir: String, target: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.preparation",
        vec!["validate".to_string(), project_dir, target],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法完成 PDBQT 自动准备前置检查。", &error),
    }
}

#[tauri::command]
async fn get_preparation_tool_status(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.preparation",
        vec!["tool-status".to_string(), project_dir],
        Duration::from_millis(500),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 PDBQT 自动准备工具能力。", &error),
    }
}

#[tauri::command]
async fn prepare_ligand_pdbqt(project_dir: String, overwrite: bool) -> String {
    match run_backend_module_async(
        "dockstart_core.preparation",
        vec![
            "prepare-ligand".to_string(),
            project_dir,
            overwrite.to_string(),
        ],
    )
    .await
    {
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
async fn prepare_receptor_pdbqt(project_dir: String, overwrite: bool) -> String {
    match run_backend_module_async(
        "dockstart_core.preparation",
        vec![
            "prepare-receptor".to_string(),
            project_dir,
            overwrite.to_string(),
        ],
    )
    .await
    {
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
    match run_backend_module_cached_async(
        "dockstart_core.project",
        vec!["run-preflight".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法完成运行驾驶舱检查。", &error),
    }
}

#[tauri::command]
async fn prepare_vina_run(project_dir: String) -> String {
    match run_backend_module_async(
        "dockstart_core.project",
        vec!["prepare-run".to_string(), project_dir],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法准备运行记录。", &error),
    }
}

#[tauri::command]
async fn get_project_workflow_status(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.project",
        vec!["workflow-status".to_string(), project_dir],
        Duration::from_millis(250),
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
    let task = tauri::async_runtime::spawn_blocking(move || {
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
async fn cancel_vina_run(project_dir: String, run_id: String) -> String {
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
async fn get_viewer_file_status(project_dir: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.viewer",
        vec!["file-status".to_string(), project_dir],
        Duration::from_millis(200),
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 3D Viewer 文件状态。", &error),
    }
}

#[tauri::command]
async fn load_structure_for_viewer(project_dir: String, file_kind: String) -> String {
    match run_backend_module_async(
        "dockstart_core.viewer",
        vec!["load-structure".to_string(), project_dir, file_kind],
    )
    .await
    {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 3D Viewer 结构文件。", &error),
    }
}

#[tauri::command]
async fn list_docking_poses(project_dir: String, run_id: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.viewer",
        vec!["list-poses".to_string(), project_dir, run_id],
        Duration::from_millis(200),
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
) -> String {
    let mut args = vec!["load-pose".to_string(), project_dir, run_id];
    if let Some(value) = mode {
        args.push(value.to_string());
    }
    match run_backend_module_async("dockstart_core.viewer", args).await {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 docking pose 内容。", &error),
    }
}

#[tauri::command]
async fn load_pose_score_summary(project_dir: String, run_id: String) -> String {
    match run_backend_module_cached_async(
        "dockstart_core.viewer",
        vec!["score-summary".to_string(), project_dir, run_id],
        Duration::from_millis(200),
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

#[derive(Default)]
struct BackendReadCache {
    ready: HashMap<String, (Instant, Result<String, String>)>,
    in_flight: HashSet<String>,
    generation: u64,
}

static BACKEND_READ_CACHE: OnceLock<(Mutex<BackendReadCache>, Condvar)> = OnceLock::new();

fn backend_read_cache() -> &'static (Mutex<BackendReadCache>, Condvar) {
    BACKEND_READ_CACHE.get_or_init(|| (Mutex::new(BackendReadCache::default()), Condvar::new()))
}

fn invalidate_backend_read_cache() {
    let (mutex, wake) = backend_read_cache();
    let mut state = mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    state.ready.clear();
    state.generation = state.generation.wrapping_add(1);
    wake.notify_all();
}

fn backend_cache_key(module: &str, args: &[String]) -> String {
    format!("{module}\u{1f}{}", args.join("\u{1f}"))
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
    let key = backend_cache_key(module, &args);
    let (mutex, wake) = backend_read_cache();

    let request_generation = loop {
        let mut state = mutex
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        state
            .ready
            .retain(|_, (stored_at, _)| stored_at.elapsed() <= Duration::from_secs(10));
        if let Some((stored_at, result)) = state.ready.get(&key) {
            if stored_at.elapsed() <= ttl {
                return result.clone();
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
        let generation = state.generation;
        drop(state);
        break generation;
    };

    let result = runner(module, args);
    let mut state = mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    state.in_flight.remove(&key);
    if state.generation == request_generation {
        state.ready.insert(key, (Instant::now(), result.clone()));
    }
    wake.notify_all();
    result
}

async fn run_backend_module_async(
    module: &'static str,
    args: Vec<String>,
) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || run_backend_module(module, args))
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
    // Every uncached call may mutate project/settings/runtime state. Clear on
    // both sides so a read that races the mutation can neither reuse an old
    // value nor publish a value captured halfway through the write.
    invalidate_backend_read_cache();
    let result = run_backend_module_uncached(module, args);
    invalidate_backend_read_cache();
    result
}

fn run_backend_module_uncached(module: &str, args: Vec<String>) -> Result<String, String> {
    let backend_dir = find_backend_dir().ok_or_else(|| {
        "未找到 Python 后端目录。请确认应用仍位于 DockStart 项目结构中。".to_string()
    })?;

    let mut errors = Vec::new();
    for python in python_candidates(&backend_dir) {
        match run_python_module(&backend_dir, &python, module, &args) {
            Ok(payload) => return Ok(payload),
            Err(error) => errors.push(format!("{python}: {error}")),
        }
    }

    Err(errors.join("\n"))
}

fn run_python_module(
    backend_dir: &Path,
    python: &str,
    module: &str,
    args: &[String],
) -> Result<String, String> {
    let mut command = build_python_module_command(backend_dir, python, module, args);

    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    let output = command.output().map_err(|error| error.to_string())?;

    if output.status.success() {
        return String::from_utf8(output.stdout).map_err(|error| error.to_string());
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    Err(format!("stdout:\n{stdout}\nstderr:\n{stderr}"))
}

fn build_python_module_command(
    backend_dir: &Path,
    python: &str,
    module: &str,
    args: &[String],
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

    candidates.push("python".to_string());
    candidates.push("python3".to_string());
    candidates
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
    let settings_path = env::var(SETTINGS_ENV_VAR)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .map(PathBuf::from)?;
    let content = fs::read_to_string(settings_path).ok()?;
    let value = json_string_value(&content, "python")?;
    if value.trim().is_empty() {
        None
    } else {
        Some(value)
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

fn find_backend_dir() -> Option<PathBuf> {
    let mut starts = Vec::new();

    if let Ok(resource_dir) = env::var(RESOURCE_DIR_ENV_VAR) {
        starts.push(PathBuf::from(resource_dir));
    }

    if let Ok(current_dir) = env::current_dir() {
        starts.push(current_dir);
    }

    if let Ok(current_exe) = env::current_exe() {
        if let Some(parent) = current_exe.parent() {
            starts.push(parent.to_path_buf());
        }
    }

    for start in starts {
        for ancestor in start.ancestors() {
            let backend_dir = ancestor.join("backend");
            if is_backend_dir(&backend_dir) {
                return Some(backend_dir);
            }
        }
    }

    None
}

fn is_backend_dir(path: &Path) -> bool {
    path.join("dockstart_core").join("tool_check.py").exists()
        && path.join("adapters").join("__init__.py").exists()
}

fn fallback_check_error_json(message: &str, raw_error: &str) -> String {
    format!(
        "[{{\"key\":\"tool_check_backend\",\"name\":\"Python 后端工具检测\",\"status\":\"error\",\"version\":\"\",\"path\":\"\",\"message\":\"{}\",\"raw_error\":\"{}\"}}]",
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
        "{{\"ok\":false,\"project\":null,\"error\":{{\"code\":\"PYTHON_BACKEND_ERROR\",\"message\":\"{}\",\"raw_error\":\"{}\",\"suggestion\":\"请确认 Python 后端可以运行。\"}}}}",
        json_escape(message),
        json_escape(raw_error)
    )
}

fn fallback_toolchain_error_json(message: &str, raw_error: &str) -> String {
    format!(
        "{{\"ok\":false,\"runtime_mode\":\"unknown\",\"resource_dir\":\"\",\"toolchain_root\":\"\",\"tools_dir\":\"\",\"licenses_dir\":\"\",\"manifest_file\":\"\",\"manifest_exists\":false,\"manifest\":{{}},\"manifest_error\":\"\",\"bundled_vina\":{{\"exists\":false,\"path\":\"\",\"version\":\"\",\"status\":\"error\",\"message\":\"{}\",\"raw_error\":\"{}\"}},\"active_vina\":null,\"active_source\":\"unknown\",\"licenses\":{{\"exists\":false,\"third_party_notices\":\"\",\"third_party_notices_exists\":false}},\"resources\":{{\"exists\":false,\"tools_dir_exists\":false,\"vina_dir_exists\":false}},\"full_status\":\"missing\",\"message\":\"{}\",\"error\":{{\"code\":\"PYTHON_BACKEND_ERROR\",\"message\":\"{}\",\"raw_error\":\"{}\",\"suggestion\":\"请确认 Python 后端可以运行。\"}}}}",
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
        .setup(|app| {
            configure_resource_dir_env(app);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
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
            create_project,
            load_project,
            import_receptor_pdbqt,
            import_ligand_pdbqt,
            fetch_pdb_structure,
            fetch_pubchem_ligand,
            get_raw_files_status,
            import_receptor_raw_file,
            import_ligand_raw_file,
            clear_receptor_raw_record,
            clear_ligand_raw_record,
            get_preparation_status,
            validate_preparation_prerequisites,
            get_preparation_tool_status,
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
            get_vina_config_preview,
            generate_vina_config,
            validate_run_prerequisites,
            get_run_preflight,
            prepare_vina_run,
            get_project_workflow_status,
            load_run_metadata,
            execute_prepared_vina_run,
            get_run_runtime_status,
            cancel_vina_run,
            get_run_files_status,
            analyze_vina_run_results,
            load_scores_csv,
            export_markdown_report,
            get_report_status,
            get_viewer_file_status,
            load_structure_for_viewer,
            list_docking_poses,
            load_docking_pose_for_viewer,
            load_pose_score_summary,
            get_box_visualization,
            update_box_from_visualization
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
}
