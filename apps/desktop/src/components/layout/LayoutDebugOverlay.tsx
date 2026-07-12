import { useCallback, useEffect, useState } from "react";

type LayoutRect = {
  id: string;
  label: string;
  left: number;
  top: number;
  width: number;
  height: number;
};

const LAYOUT_SELECTOR = "[data-layout]";
const LAYOUT_TOLERANCE = 2;
const MIN_PANEL_HEIGHT = 300;
const MIN_RAIL_HEIGHT_RATIO = 0.7;
const MIN_VIEWPORT_FILL_RATIO = 0.45;
const LEGACY_LAYOUT_SELECTOR = ".workbench-page, .task-layout, .step-task-layout, .context-panel, .context-panel-shell";

function cssNumber(name: string, fallback: number): number {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function collectRects(): LayoutRect[] {
  return Array.from(document.querySelectorAll<HTMLElement>(LAYOUT_SELECTOR)).map((element, index) => {
    const rect = element.getBoundingClientRect();
    return {
      id: `${element.dataset.layout ?? "layout"}-${index}`,
      label: element.dataset.layout ?? "layout",
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
    };
  });
}

function warnLayout(message: string, detail: Record<string, unknown>) {
  // eslint-disable-next-line no-console
  console.warn(`[DockStart layout] ${message}`, detail);
}

function isFloatingCard(element: HTMLElement): boolean {
  const style = getComputedStyle(element);
  const transparentBackground = getComputedStyle(document.documentElement)
    .getPropertyValue("--ds-color-transparent")
    .trim();
  const backgroundVisible = style.backgroundColor !== transparentBackground && style.backgroundColor !== "transparent";
  const hasShadow = style.boxShadow !== "none";
  const hasRadius = Number.parseFloat(style.borderTopLeftRadius) > 0;
  return (backgroundVisible || hasShadow) && hasRadius;
}

function checkButtonWidths(group: HTMLElement, selector: string, label: string) {
  const widths = Array.from(group.querySelectorAll<HTMLElement>(selector))
    .map((button) => button.getBoundingClientRect().width)
    .filter((width) => Number.isFinite(width));
  if (widths.length < 2) return;
  const min = Math.min(...widths);
  const max = Math.max(...widths);
  if (max - min > LAYOUT_TOLERANCE) {
    warnLayout("同类按钮宽度不一致。", { buttonClass: label, minWidth: min, maxWidth: max, delta: max - min });
  }
}

function runLayoutLint() {
  const pageShell = document.querySelector<HTMLElement>('[data-layout="page-shell"]');
  const pageHero = document.querySelector<HTMLElement>('[data-layout="page-hero"]');
  const bodyGrid = document.querySelector<HTMLElement>('[data-layout="body-grid"]');
  const mainPanel = document.querySelector<HTMLElement>('[data-layout="main-panel"]');
  const rightRail = document.querySelector<HTMLElement>('[data-layout="right-rail"]');
  const modeTabs = document.querySelector<HTMLElement>('[data-layout="mode-tabs"]');

  const pageMax = cssNumber("--ds-page-max", 1040);
  const expectedGap = cssNumber("--ds-body-gap", 20);

  if (pageShell) {
    const rect = pageShell.getBoundingClientRect();
    if (rect.width - pageMax > LAYOUT_TOLERANCE) {
      warnLayout("PageShell 宽度超过 token。", { width: rect.width, expectedMax: pageMax });
    }
    if (rect.height < window.innerHeight * MIN_VIEWPORT_FILL_RATIO) {
      warnLayout("页面主要内容高度低于视口 45%，可能显得过空。", {
        pageHeight: rect.height,
        viewportHeight: window.innerHeight,
        ratio: rect.height / window.innerHeight,
      });
    }
    if (!pageHero) {
      warnLayout("PageShell 缺少 PageHero。", {});
    }
    if (!bodyGrid) {
      warnLayout("PageShell 缺少 BodyGrid。", {});
    }
  }

  if (bodyGrid && (!mainPanel || !rightRail)) {
    warnLayout("BodyGrid 应包含 MainPanel 和 RightRail。", {
      hasMainPanel: Boolean(mainPanel),
      hasRightRail: Boolean(rightRail),
    });
  }

  if (pageHero && bodyGrid) {
    const heroRect = pageHero.getBoundingClientRect();
    const gridRect = bodyGrid.getBoundingClientRect();
    if (Math.abs(heroRect.left - gridRect.left) > LAYOUT_TOLERANCE || Math.abs(heroRect.width - gridRect.width) > LAYOUT_TOLERANCE) {
      warnLayout("PageHero 宽度或左边界未和 BodyGrid 对齐。", {
        heroLeft: heroRect.left,
        gridLeft: gridRect.left,
        heroWidth: heroRect.width,
        gridWidth: gridRect.width,
      });
    }
  }

  if (mainPanel && rightRail) {
    const mainRect = mainPanel.getBoundingClientRect();
    const railRect = rightRail.getBoundingClientRect();
    const actualGap = railRect.left - mainRect.right;
    if (mainRect.height < MIN_PANEL_HEIGHT) {
      warnLayout("MainPanel 高度小于 300px。", { mainHeight: mainRect.height });
    }
    if (railRect.height < mainRect.height * MIN_RAIL_HEIGHT_RATIO) {
      warnLayout("RightRail 高度小于 MainPanel 的 70%。", {
        mainHeight: mainRect.height,
        railHeight: railRect.height,
        ratio: railRect.height / mainRect.height,
      });
    }
    if (Math.abs(actualGap - expectedGap) > LAYOUT_TOLERANCE) {
      warnLayout("MainPanel 与 RightRail 间距不符合 token。", { actualGap, expectedGap });
    }
    if (Math.abs(mainRect.top - railRect.top) > LAYOUT_TOLERANCE) {
      warnLayout("MainPanel 与 RightRail 顶部未对齐。", { mainTop: mainRect.top, railTop: railRect.top });
    }
  }

  if (mainPanel && modeTabs) {
    if (!mainPanel.contains(modeTabs)) {
      warnLayout("ModeTabs 不在 MainPanel 内部。", {});
    }
    const mainRect = mainPanel.getBoundingClientRect();
    const tabsRect = modeTabs.getBoundingClientRect();
    if (Math.abs(mainRect.left - tabsRect.left) > LAYOUT_TOLERANCE || Math.abs(mainRect.right - tabsRect.right) > LAYOUT_TOLERANCE) {
      warnLayout("ModeTabs 左右边界未和 MainPanel 对齐。", {
        mainLeft: mainRect.left,
        tabsLeft: tabsRect.left,
        mainRight: mainRect.right,
        tabsRight: tabsRect.right,
      });
    }
  }

  document.querySelectorAll<HTMLElement>('[data-layout="main-panel"]').forEach((panel) => {
    const floatingTopCards = Array.from(panel.querySelectorAll<HTMLElement>(":scope > .main-panel-content > .section-card"))
      .filter(isFloatingCard);
    if (floatingTopCards.length > 1) {
      warnLayout("MainPanel 内存在多个顶层浮动 SectionCard。", { count: floatingTopCards.length });
    }

    const rightEdges = Array.from(panel.querySelectorAll<HTMLElement>('[data-layout="form-row"]'))
      .map((row) => row.getBoundingClientRect().right)
      .filter((right) => Number.isFinite(right));
    if (rightEdges.length < 2) return;
    const min = Math.min(...rightEdges);
    const max = Math.max(...rightEdges);
    if (max - min > LAYOUT_TOLERANCE) {
      warnLayout("表单输入框右边界不一致。", { minRight: min, maxRight: max, delta: max - min });
    }
  });

  document.querySelectorAll<HTMLElement>('[data-layout="action-button-group"], .button-row, .page-hero-actions').forEach((group) => {
    ["primary-button", "secondary-button"].forEach((buttonClass) => {
      checkButtonWidths(group, `[data-layout="action-button"].${buttonClass}`, buttonClass);
    });
  });

  document.querySelectorAll<HTMLElement>(".start-route-grid, .demo-project-list").forEach((group) => {
    checkButtonWidths(group, ".secondary-button", "secondary-button");
  });

  document.querySelectorAll<HTMLElement>(LEGACY_LAYOUT_SELECTOR).forEach((element) => {
    if (element.offsetParent === null) return;
    warnLayout("页面存在 legacy layout class。", { className: element.className });
  });
}

export default function LayoutDebugOverlay() {
  const [visible, setVisible] = useState(false);
  const [rects, setRects] = useState<LayoutRect[]>([]);

  const refresh = useCallback(() => {
    if (!visible) return;
    setRects(collectRects());
    runLayoutLint();
  }, [visible]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "g") {
        event.preventDefault();
        setVisible((value) => !value);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    refresh();
    if (!visible) return undefined;
    const onUpdate = () => refresh();
    window.addEventListener("resize", onUpdate);
    window.addEventListener("scroll", onUpdate, true);
    const timer = window.setInterval(refresh, 500);
    return () => {
      window.removeEventListener("resize", onUpdate);
      window.removeEventListener("scroll", onUpdate, true);
      window.clearInterval(timer);
    };
  }, [refresh, visible]);

  if (!visible) return null;

  return (
    <div className="layout-debug-overlay" aria-hidden="true">
      <div className="layout-debug-grid" />
      {rects.map((rect) => (
        <div
          className="layout-debug-box"
          key={rect.id}
          style={{
            left: rect.left,
            top: rect.top,
            width: rect.width,
            height: rect.height,
          }}
        >
          <span>{rect.label}</span>
        </div>
      ))}
    </div>
  );
}
