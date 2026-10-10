import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import { Minus, Square, X } from "@phosphor-icons/react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import Tooltip from "../components/Tooltip";

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
  useLanguage();
  return (
    <div className="window-controls" aria-label={translate("窗口控制")}>
      <Tooltip className="window-control-tooltip" label={translate("最小化窗口")}>
        <button type="button" aria-label={translate("最小化窗口")} onClick={() => void runWindowAction("minimize")}>
          <Minus aria-hidden="true" size={16} />
        </button>
      </Tooltip>
      <Tooltip className="window-control-tooltip" label={translate("最大化或还原窗口")}>
        <button type="button" aria-label={translate("最大化或还原窗口")} onClick={() => void runWindowAction("maximize")}>
          <Square aria-hidden="true" size={13} />
        </button>
      </Tooltip>
      <Tooltip className="window-control-tooltip" label={translate("关闭窗口")}>
        <button className="window-close-button" type="button" aria-label={translate("关闭窗口")} onClick={() => void runWindowAction("close")}>
          <X aria-hidden="true" size={16} />
        </button>
      </Tooltip>
    </div>
  );
}
