import { useCallback } from "react";
import { open } from "@tauri-apps/plugin-dialog";

type FileFilter = {
  name: string;
  extensions: string[];
};

type PathInputProps = {
  /** 当前路径值 */
  value: string;
  /** 路径变化回调 */
  onChange: (value: string) => void;
  /** 输入框占位文字 */
  placeholder?: string;
  /** 是否禁用输入与按钮 */
  disabled?: boolean;
  /**
   * 选择类型：
   * - "file"（默认）：打开文件选择对话框
   * - "directory"：打开目录选择对话框
   */
  mode?: "file" | "directory";
  /** 文件过滤器，仅 mode="file" 生效。例如 [{ name: "PDBQT", extensions: ["pdbqt"] }] */
  filters?: FileFilter[];
  /** 原生对话框标题 */
  title?: string;
  /** 输入框 id，用于 <label htmlFor> 关联 */
  id?: string;
  /** 输入框 aria-label，无可见 label 时使用 */
  ariaLabel?: string;
};

/**
 * 路径输入组件：文本输入框 + “选择…”按钮。
 *
 * 点击按钮调用 tauri-plugin-dialog 的 open() 打开原生文件/目录选择对话框，
 * 把选中的绝对路径写回输入框。用户可继续手动编辑路径。
 *
 * 非 Tauri 环境（如纯浏览器开发预览）下 open() 会抛错，此处静默忽略，
 * 不影响手动输入。
 */
export default function PathInput({
  value,
  onChange,
  placeholder,
  disabled = false,
  mode = "file",
  filters,
  title,
  id,
  ariaLabel,
}: PathInputProps) {
  const pickPath = useCallback(async () => {
    if (disabled) {
      return;
    }
    try {
      const selected = await open({
        directory: mode === "directory",
        multiple: false,
        filters: mode === "file" ? filters : undefined,
        title: title ?? (mode === "directory" ? "选择文件夹" : "选择文件"),
      });
      if (selected === null || selected === undefined) {
        return;
      }
      // open() 在 multiple:false 时返回 string；保险起见处理数组情况。
      const nextPath = Array.isArray(selected) ? selected[0] ?? "" : selected;
      if (nextPath) {
        onChange(nextPath);
      }
    } catch {
      // 非 Tauri 桌面环境（例如 vite dev server 纯浏览器预览）下插件不可用，
      // 忽略错误，用户仍可手动输入路径。
    }
  }, [disabled, filters, mode, onChange, title]);

  return (
    <div className="path-input" data-layout="form-row">
      <input
        id={id}
        type="text"
        className="path-input-field"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        aria-label={ariaLabel}
      />
      <button
        type="button"
        className="secondary-button path-input-button"
        disabled={disabled}
        onClick={() => void pickPath()}
        title={mode === "directory" ? "选择文件夹" : "选择文件"}
      >
        选择…
      </button>
    </div>
  );
}
