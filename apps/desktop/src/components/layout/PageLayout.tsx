import type { ReactNode } from "react";

type PageShellProps = {
  labelledBy?: string;
  className?: string;
  children: ReactNode;
};

type PageHeroProps = {
  eyebrow?: string;
  title: string;
  titleId?: string;
  description: string;
  actions?: ReactNode;
};

type BodyGridProps = {
  children: ReactNode;
  className?: string;
};

type PanelProps = {
  children: ReactNode;
  className?: string;
};

type ModeTabsProps<TMode extends string> = {
  label: string;
  options: Array<{ id: TMode; label: string }>;
  active: TMode;
  onChange: (mode: TMode) => void;
};

export function PageShell({ labelledBy, className = "", children }: PageShellProps) {
  return (
    <section
      aria-labelledby={labelledBy}
      className={`page-shell ${className}`.trim()}
      data-layout="page-shell"
    >
      {children}
    </section>
  );
}

export function PageHero({ eyebrow, title, titleId, description, actions }: PageHeroProps) {
  return (
    <header className="page-hero" data-layout="page-hero">
      <div className="page-hero-main">
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1 id={titleId}>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="page-hero-actions">{actions}</div> : null}
    </header>
  );
}

export function BodyGrid({ children, className = "" }: BodyGridProps) {
  return (
    <div className={`page-body-grid ${className}`.trim()} data-layout="body-grid">
      {children}
    </div>
  );
}

export function MainPanel({ children, className = "" }: PanelProps) {
  return (
    <main className={`main-panel ${className}`.trim()} data-layout="main-panel">
      {children}
    </main>
  );
}

export function RightRail({ children, className = "" }: PanelProps) {
  return (
    <aside className={`right-rail ${className}`.trim()} data-layout="right-rail">
      {children}
    </aside>
  );
}

export function RightRailSection({ children, className = "", title }: PanelProps & { title: string }) {
  return (
    <section className={`right-rail-section ${className}`.trim()}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

export function ModeTabs<TMode extends string>({ label, options, active, onChange }: ModeTabsProps<TMode>) {
  return (
    <div className="mode-tabs" aria-label={label} data-layout="mode-tabs" role="tablist">
      {options.map((option) => (
        <button
          aria-selected={active === option.id}
          className={active === option.id ? "active" : ""}
          key={option.id}
          onClick={() => onChange(option.id)}
          role="tab"
          type="button"
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
