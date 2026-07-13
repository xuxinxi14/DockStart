import { Minus, Square, X } from "@phosphor-icons/react";
import { getCurrentWindow } from "@tauri-apps/api/window";

async function runWindowAction(action: "minimize" | "maximize" | "close") {
  const window = getCurrentWindow();
  try {
    if (action === "minimize") await window.minimize();
    else if (action === "maximize") await window.toggleMaximize();
    else await window.close();
  } catch (error) {
    if (import.meta.env.DEV) console.warn(`窗口操作失败：${action}`, error);
  }
}

export default function WindowControls() {
  return (
    <div className="window-controls" aria-label="窗口控制">
      <button type="button" title="最小化" aria-label="最小化窗口" onClick={() => void runWindowAction("minimize")}>
        <Minus aria-hidden="true" size={16} />
      </button>
      <button type="button" title="最大化或还原" aria-label="最大化或还原窗口" onClick={() => void runWindowAction("maximize")}>
        <Square aria-hidden="true" size={13} />
      </button>
      <button className="window-close-button" type="button" title="关闭" aria-label="关闭窗口" onClick={() => void runWindowAction("close")}>
        <X aria-hidden="true" size={16} />
      </button>
    </div>
  );
}
