#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    env,
    path::{Path, PathBuf},
    process::Command,
};

#[tauri::command]
fn check_tools() -> String {
    match run_backend_module("dockstart_core.tool_check", Vec::new()) {
        Ok(payload) => payload,
        Err(error) => fallback_check_error_json("无法调用 Python 后端工具检测入口。", &error),
    }
}

#[tauri::command]
fn get_toolchain_status() -> String {
    match run_backend_module("dockstart_core.toolchain", Vec::new()) {
        Ok(payload) => payload,
        Err(error) => fallback_toolchain_error_json("无法读取 DockStart 内置工具链状态。", &error),
    }
}

#[tauri::command]
fn get_settings() -> String {
    match run_backend_module("dockstart_core.settings", vec!["get".to_string()]) {
        Ok(payload) => payload,
        Err(error) => fallback_settings_error_json("无法读取 DockStart 设置。", &error),
    }
}

#[tauri::command]
fn save_settings(settings_json: String) -> String {
    match run_backend_module(
        "dockstart_core.settings",
        vec!["save-json".to_string(), settings_json],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_settings_error_json("无法保存 DockStart 设置。", &error),
    }
}

#[tauri::command]
fn update_tool_path(tool_key: String, path: String) -> String {
    match run_backend_module(
        "dockstart_core.settings",
        vec!["update-tool-path".to_string(), tool_key, path],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_settings_error_json("无法更新工具路径。", &error),
    }
}

#[tauri::command]
fn create_project(project_name: String, base_dir: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["create".to_string(), project_name, base_dir],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法创建 DockStart 项目。", &error),
    }
}

#[tauri::command]
fn load_project(project_dir: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["load".to_string(), project_dir],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 DockStart 项目。", &error),
    }
}

#[tauri::command]
fn import_receptor_pdbqt(project_dir: String, source_path: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["import-receptor".to_string(), project_dir, source_path],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导入受体 PDBQT。", &error),
    }
}

#[tauri::command]
fn import_ligand_pdbqt(project_dir: String, source_path: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["import-ligand".to_string(), project_dir, source_path],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导入配体 PDBQT。", &error),
    }
}

#[tauri::command]
fn get_box_params(project_dir: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["get-box".to_string(), project_dir],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 Box 参数。", &error),
    }
}

#[tauri::command]
fn update_box_params(project_dir: String, box_json: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["update-box".to_string(), project_dir, box_json],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法保存 Box 参数。", &error),
    }
}

#[tauri::command]
fn get_vina_params(project_dir: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["get-vina".to_string(), project_dir],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 Vina 参数。", &error),
    }
}

#[tauri::command]
fn update_vina_params(project_dir: String, vina_json: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["update-vina".to_string(), project_dir, vina_json],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法保存 Vina 参数。", &error),
    }
}

#[tauri::command]
fn get_vina_config_preview(project_dir: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["preview-config".to_string(), project_dir],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法生成 Vina 配置预览。", &error),
    }
}

#[tauri::command]
fn generate_vina_config(project_dir: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["generate-config".to_string(), project_dir],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法生成 vina_config.txt。", &error),
    }
}

#[tauri::command]
fn validate_run_prerequisites(project_dir: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["validate-run".to_string(), project_dir],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法完成运行前检查。", &error),
    }
}

#[tauri::command]
fn prepare_vina_run(project_dir: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["prepare-run".to_string(), project_dir],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法准备运行记录。", &error),
    }
}

#[tauri::command]
fn load_run_metadata(project_dir: String, run_id: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["load-run-metadata".to_string(), project_dir, run_id],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取运行元数据。", &error),
    }
}

#[tauri::command]
fn execute_prepared_vina_run(project_dir: String, run_id: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["execute-run".to_string(), project_dir, run_id],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法执行 prepared Vina run。", &error),
    }
}

#[tauri::command]
fn get_run_files_status(project_dir: String, run_id: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["run-files-status".to_string(), project_dir, run_id],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取运行文件状态。", &error),
    }
}

#[tauri::command]
fn analyze_vina_run_results(project_dir: String, run_id: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["analyze-results".to_string(), project_dir, run_id],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法解析 Vina 结果。", &error),
    }
}

#[tauri::command]
fn load_scores_csv(project_dir: String, run_id: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["load-scores".to_string(), project_dir, run_id],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取 scores.csv。", &error),
    }
}

#[tauri::command]
fn export_markdown_report(project_dir: String, run_id: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["export-report".to_string(), project_dir, run_id],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法导出 Markdown 报告。", &error),
    }
}

#[tauri::command]
fn get_report_status(project_dir: String, run_id: String) -> String {
    match run_backend_module(
        "dockstart_core.project",
        vec!["report-status".to_string(), project_dir, run_id],
    ) {
        Ok(payload) => payload,
        Err(error) => fallback_project_error_json("无法读取报告状态。", &error),
    }
}

fn run_backend_module(module: &str, args: Vec<String>) -> Result<String, String> {
    let backend_dir = find_backend_dir()
        .ok_or_else(|| "未找到 Python 后端目录。请确认应用仍位于 DockStart 项目结构中。".to_string())?;

    let mut errors = Vec::new();
    for python in ["python", "python3"] {
        match run_python_module(&backend_dir, python, module, &args) {
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
    let output = Command::new(python)
        .arg("-m")
        .arg(module)
        .args(args)
        .current_dir(backend_dir)
        .env("PYTHONIOENCODING", "utf-8")
        .output()
        .map_err(|error| error.to_string())?;

    if output.status.success() {
        return String::from_utf8(output.stdout).map_err(|error| error.to_string());
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    Err(format!("stdout:\n{stdout}\nstderr:\n{stderr}"))
}

fn find_backend_dir() -> Option<PathBuf> {
    let mut starts = Vec::new();

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
            let tool_check = backend_dir.join("dockstart_core").join("tool_check.py");
            if tool_check.exists() {
                return Some(backend_dir);
            }
        }
    }

    None
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
        "{{\"ok\":false,\"toolchain_root\":\"\",\"tools_dir\":\"\",\"licenses_dir\":\"\",\"manifest_file\":\"\",\"manifest_exists\":false,\"manifest\":{{}},\"manifest_error\":\"\",\"bundled_vina\":{{\"exists\":false,\"path\":\"\",\"version\":\"\",\"status\":\"error\",\"message\":\"{}\",\"raw_error\":\"{}\"}},\"active_vina\":null,\"active_source\":\"unknown\",\"licenses\":{{\"exists\":false,\"third_party_notices\":\"\",\"third_party_notices_exists\":false}},\"resources\":{{\"exists\":false,\"tools_dir_exists\":false,\"vina_dir_exists\":false}},\"full_status\":\"missing\",\"message\":\"{}\",\"error\":{{\"code\":\"PYTHON_BACKEND_ERROR\",\"message\":\"{}\",\"raw_error\":\"{}\",\"suggestion\":\"请确认 Python 后端可以运行。\"}}}}",
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

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            check_tools,
            get_toolchain_status,
            get_settings,
            save_settings,
            update_tool_path,
            create_project,
            load_project,
            import_receptor_pdbqt,
            import_ligand_pdbqt,
            get_box_params,
            update_box_params,
            get_vina_params,
            update_vina_params,
            get_vina_config_preview,
            generate_vina_config,
            validate_run_prerequisites,
            prepare_vina_run,
            load_run_metadata,
            execute_prepared_vina_run,
            get_run_files_status,
            analyze_vina_run_results,
            load_scores_csv,
            export_markdown_report,
            get_report_status
        ])
        .run(tauri::generate_context!())
        .expect("error while running DockStart");
}
