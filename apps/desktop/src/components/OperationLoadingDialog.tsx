import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { SpinnerGap } from "@phosphor-icons/react";
import ActionButton from "./ActionButton";

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

  useEffect(() => {
    if (!open) return;
    const appRoot = document.getElementById("root");
    const wasInert = appRoot?.inert ?? false;
    const previousOverflow = document.body.style.overflow;
    if (appRoot) appRoot.inert = true;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();
    return () => {
      if (appRoot) appRoot.inert = wasInert;
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div className="operation-loading-backdrop">
      <div
        aria-describedby={descriptionId}
        aria-labelledby={titleId}
        aria-modal="true"
        className="operation-loading-dialog"
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
