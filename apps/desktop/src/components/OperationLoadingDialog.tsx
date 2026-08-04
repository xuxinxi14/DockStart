import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { SpinnerGap } from "@phosphor-icons/react";
import ActionButton from "./ActionButton";

const DISPLAY_DELAY_MS = 230;

let modalLockCount = 0;
let lockedAppRoot: HTMLElement | null = null;
let appRootWasInert = false;
let previousBodyOverflow = "";

function acquireModalLock() {
  if (modalLockCount === 0) {
    lockedAppRoot = document.getElementById("root");
    appRootWasInert = lockedAppRoot?.inert ?? false;
    previousBodyOverflow = document.body.style.overflow;
    if (lockedAppRoot) lockedAppRoot.inert = true;
    document.body.style.overflow = "hidden";
  }

  modalLockCount += 1;
  let released = false;

  return () => {
    if (released) return;
    released = true;
    modalLockCount = Math.max(0, modalLockCount - 1);
    if (modalLockCount > 0) return;

    if (lockedAppRoot) lockedAppRoot.inert = appRootWasInert;
    document.body.style.overflow = previousBodyOverflow;
    lockedAppRoot = null;
    appRootWasInert = false;
    previousBodyOverflow = "";
  };
}

type OperationLoadingDialogProps = {
  open: boolean;
  title: string;
  message: string;
  detail?: string;
  actionLabel?: string;
  onAction?: () => void;
};

export default function OperationLoadingDialog({
  open,
  title,
  message,
  detail,
  actionLabel,
  onAction,
}: OperationLoadingDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);
  const [visible, setVisible] = useState(false);
  const hasAction = Boolean(actionLabel && onAction);

  useEffect(() => {
    if (!open) {
      setVisible(false);
      return;
    }

    const timer = window.setTimeout(() => setVisible(true), DISPLAY_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!open || !visible) return;

    const activeElement = document.activeElement;
    previouslyFocusedRef.current =
      activeElement instanceof HTMLElement && activeElement !== document.body ? activeElement : null;

    const releaseModalLock = acquireModalLock();
    const focusFrame = window.requestAnimationFrame(() => {
      dialogRef.current?.focus({ preventScroll: true });
    });

    return () => {
      window.cancelAnimationFrame(focusFrame);
      releaseModalLock();

      const focusTarget = previouslyFocusedRef.current;
      previouslyFocusedRef.current = null;
      if (focusTarget?.isConnected && !focusTarget.closest("[inert]")) {
        focusTarget.focus({ preventScroll: true });
      }
    };
  }, [open, visible]);

  const handleDialogKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      return;
    }

    if (event.key !== "Tab" || !hasAction) return;

    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusableElements = Array.from(
      dialog.querySelectorAll<HTMLElement>(
        "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
      ),
    ).filter((element) => element.getAttribute("aria-hidden") !== "true" && element.tabIndex >= 0);

    if (focusableElements.length === 0) {
      event.preventDefault();
      dialog.focus({ preventScroll: true });
      return;
    }

    const firstElement = focusableElements[0];
    const lastElement = focusableElements[focusableElements.length - 1];
    const activeElement = document.activeElement;
    const focusIsOutsideDialog = !activeElement || !dialog.contains(activeElement);

    if (event.shiftKey && (focusIsOutsideDialog || activeElement === dialog || activeElement === firstElement)) {
      event.preventDefault();
      lastElement.focus({ preventScroll: true });
    } else if (!event.shiftKey && (focusIsOutsideDialog || activeElement === dialog || activeElement === lastElement)) {
      event.preventDefault();
      firstElement.focus({ preventScroll: true });
    }
  };

  if (!open || !visible) return null;

  return createPortal(
    <div className="operation-loading-backdrop">
      <div
        aria-describedby={descriptionId}
        aria-labelledby={titleId}
        aria-modal="true"
        className="operation-loading-dialog"
        onKeyDown={handleDialogKeyDown}
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="operation-loading-indicator" role="status">
          <SpinnerGap aria-hidden="true" className="operation-loading-spinner" size={30} weight="bold" />
        </div>
        <div className="operation-loading-copy">
          <h2 id={titleId}>{title}</h2>
          <p id={descriptionId}>{message}</p>
          {detail ? <small>{detail}</small> : null}
        </div>
        {actionLabel && onAction ? (
          <ActionButton onClick={onAction}>{actionLabel}</ActionButton>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
