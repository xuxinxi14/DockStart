import { useCallback, useId, useState } from "react";
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
  /** 输入框已有的辅助说明 id；选择器错误提示会与其合并 */
  ariaDescribedBy?: string;
};

/**
 * 路径输入组件：文本输入框 + “选择…”按钮。
 *
 * 点击按钮调用 tauri-plugin-dialog 的 open() 打开原生文件/目录选择对话框，
 * 把选中的绝对路径写回输入框。用户可继续手动编辑路径。
 *
 * 非 Tauri 环境（如纯浏览器开发预览）下 open() 会抛错，此处显示中文提示，
 * 同时保留文本框供用户手动输入。
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
  ariaDescribedBy,
}: PathInputProps) {
  const generatedId = useId().replace(/:/g, "");
  const feedbackId = `${id ?? `path-input-${generatedId}`}-picker-feedback`;
  const [pickerError, setPickerError] = useState("");
  const pickerTitle = title ?? (mode === "directory" ? "选择文件夹" : "选择文件");

  const pickPath = useCallback(async () => {
    if (disabled) {
      return;
    }
    setPickerError("");
    try {
      const selected = await open({
        directory: mode === "directory",
        multiple: false,
        filters: mode === "file" ? filters : undefined,
        title: pickerTitle,
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
      const targetLabel = mode === "directory" ? "文件夹" : "文件";
      setPickerError(
        `无法打开${targetLabel}选择器。请直接在输入框中手动填写路径；浏览器预览模式不支持系统选择器。`,
      );
    }
  }, [disabled, filters, mode, onChange, pickerTitle]);

  const describedBy = [ariaDescribedBy, pickerError ? feedbackId : undefined].filter(Boolean).join(" ") || undefined;

  return (
    <div className="path-input-group form-field">
      <div className="path-input" data-layout="form-row">
        <input
          aria-describedby={describedBy}
          aria-label={ariaLabel}
          className="path-input-field"
          disabled={disabled}
          id={id}
          onChange={(event) => {
            setPickerError("");
            onChange(event.target.value);
          }}
          placeholder={placeholder}
          type="text"
          value={value}
        />
        <button
          aria-describedby={pickerError ? feedbackId : undefined}
          aria-label={pickerTitle}
          className="secondary-button path-input-button"
          disabled={disabled}
          onClick={() => void pickPath()}
          title={pickerTitle}
          type="button"
        >
          选择…
        </button>
      </div>
      {pickerError ? (
        <p className="message-line" id={feedbackId} role="alert">
          {pickerError}
        </p>
      ) : null}
    </div>
  );
}
