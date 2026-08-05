import {
  cloneElement,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type FocusEvent,
  type ReactElement,
} from "react";
import { createPortal } from "react-dom";
import { isTooltipAvailable, mergeAriaDescribedBy } from "./tooltipAccessibility";

type TooltipPlacement = "right" | "bottom";

type TooltipProps = {
  children: ReactElement<{
    "aria-describedby"?: string;
    disabled?: boolean;
  }>;
  label: string;
  placement?: TooltipPlacement;
  className?: string;
  disabled?: boolean;
  delay?: number;
};

type TooltipPosition = {
  left: number;
  top: number;
};

const VIEWPORT_GUTTER = 8;
const TOOLTIP_GAP = 9;

export default function Tooltip({
  children,
  label,
  placement = "bottom",
  className = "",
  disabled = false,
  delay = 420,
}: TooltipProps) {
  const tooltipId = useId();
  const available = isTooltipAvailable({
    disabled,
    childDisabled: children.props.disabled === true,
    label,
  });
  const anchorRef = useRef<HTMLSpanElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const showTimerRef = useRef<number | null>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<TooltipPosition>({ left: 0, top: 0 });

  const clearShowTimer = useCallback(() => {
    if (showTimerRef.current !== null) {
      window.clearTimeout(showTimerRef.current);
      showTimerRef.current = null;
    }
  }, []);

  const updatePosition = useCallback(() => {
    const anchor = anchorRef.current;
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    setPosition(
      placement === "right"
        ? { left: rect.right + TOOLTIP_GAP, top: rect.top + rect.height / 2 }
        : { left: rect.left + rect.width / 2, top: rect.bottom + TOOLTIP_GAP },
    );
  }, [placement]);

  const show = useCallback((immediate: boolean) => {
    clearShowTimer();
    if (!available) return;
    const openTooltip = () => {
      updatePosition();
      setOpen(true);
    };
    if (immediate || delay <= 0) {
      openTooltip();
      return;
    }
    showTimerRef.current = window.setTimeout(openTooltip, delay);
  }, [available, clearShowTimer, delay, updatePosition]);

  const hide = useCallback(() => {
    clearShowTimer();
    setOpen(false);
  }, [clearShowTimer]);

  useLayoutEffect(() => {
    if (!open) return;
    const tooltip = tooltipRef.current;
    if (!tooltip) return;
    const rect = tooltip.getBoundingClientRect();
    let leftOffset = 0;
    let topOffset = 0;
    if (rect.left < VIEWPORT_GUTTER) leftOffset = VIEWPORT_GUTTER - rect.left;
    if (rect.right > window.innerWidth - VIEWPORT_GUTTER) {
      leftOffset = window.innerWidth - VIEWPORT_GUTTER - rect.right;
    }
    if (rect.top < VIEWPORT_GUTTER) topOffset = VIEWPORT_GUTTER - rect.top;
    if (rect.bottom > window.innerHeight - VIEWPORT_GUTTER) {
      topOffset = window.innerHeight - VIEWPORT_GUTTER - rect.bottom;
    }
    if (leftOffset || topOffset) {
      setPosition((current) => ({
        left: current.left + leftOffset,
        top: current.top + topOffset,
      }));
    }
  }, [open, position.left, position.top]);

  useEffect(() => {
    if (!open) return;
    const reposition = () => updatePosition();
    const closeOnScroll = () => hide();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") hide();
    };
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", closeOnScroll, true);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", closeOnScroll, true);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [hide, open, updatePosition]);

  useEffect(() => {
    if (!available) hide();
    return clearShowTimer;
  }, [available, clearShowTimer, hide]);

  function handleBlur(event: FocusEvent<HTMLSpanElement>) {
    if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
    hide();
  }

  return (
    <>
      <span
        className={`ds-tooltip-anchor ${className}`.trim()}
        onBlurCapture={handleBlur}
        onFocusCapture={() => show(true)}
        onKeyDown={(event) => {
          if (event.key === "Escape") hide();
        }}
        onPointerDown={hide}
        onPointerEnter={() => show(false)}
        onPointerLeave={hide}
        ref={anchorRef}
      >
        {available
          ? cloneElement(children, {
              "aria-describedby": mergeAriaDescribedBy(
                children.props["aria-describedby"],
                tooltipId,
              ),
            })
          : children}
      </span>
      {open
        ? createPortal(
            <div
              className={`ds-tooltip ds-tooltip-${placement}`}
              id={tooltipId}
              ref={tooltipRef}
              role="tooltip"
              style={{ left: position.left, top: position.top }}
            >
              {label}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
