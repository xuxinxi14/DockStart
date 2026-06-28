import type { ReactNode } from "react";

type CardProps = {
  children: ReactNode;
  className?: string;
  compact?: boolean;
  as?: "article" | "section" | "div";
};

export default function Card({ children, className = "", compact = false, as: Element = "section" }: CardProps) {
  return <Element className={`ds-card${compact ? " compact" : ""} ${className}`.trim()}>{children}</Element>;
}
