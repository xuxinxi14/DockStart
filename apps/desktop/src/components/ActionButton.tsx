import type { ButtonHTMLAttributes, ReactNode } from "react";

type ActionButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "text";
  children: ReactNode;
};

export default function ActionButton({ variant = "secondary", children, className = "", ...props }: ActionButtonProps) {
  const variantClass =
    variant === "primary" ? "primary-button" : variant === "text" ? "text-button inline" : "secondary-button";
  return (
    <button className={`${variantClass} ${className}`.trim()} type="button" {...props}>
      {children}
    </button>
  );
}
