import type { ReactNode } from "react";

type SectionCardProps = {
  title?: string;
  description?: string;
  children: ReactNode;
  className?: string;
};

export default function SectionCard({ title, description, children, className = "" }: SectionCardProps) {
  return (
    <section className={`section-card ${className}`.trim()} data-layout="card">
      {title || description ? (
        <div className="section-card-header">
          {title ? <h2>{title}</h2> : null}
          {description ? <p>{description}</p> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}
