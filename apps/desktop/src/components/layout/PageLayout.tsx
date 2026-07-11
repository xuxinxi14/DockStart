import { useId, type KeyboardEvent, type ReactNode } from "react";

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
  id?: string;
  label: string;
  options: Array<{ id: TMode; label: string; controlsId?: string }>;
  active: TMode;
  onChange: (mode: TMode) => void;
  orientation?: "horizontal" | "vertical";
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
    <div className={`main-panel ${className}`.trim()} data-layout="main-panel">
      {children}
    </div>
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

export function ModeTabs<TMode extends string>({
  id,
  label,
  options,
  active,
  onChange,
  orientation = "horizontal",
}: ModeTabsProps<TMode>) {
  const generatedId = useId().replace(/:/g, "");
  const tabListId = id ?? `mode-tabs-${generatedId}`;
  const hasActiveOption = options.some((option) => option.id === active);

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, currentIndex: number) => {
    if (event.altKey || event.ctrlKey || event.metaKey || options.length === 0) {
      return;
    }

    let nextIndex: number | undefined;
    const lastIndex = options.length - 1;

    if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = lastIndex;
    } else if (
      (orientation === "horizontal" && event.key === "ArrowRight") ||
      (orientation === "vertical" && event.key === "ArrowDown")
    ) {
      nextIndex = currentIndex === lastIndex ? 0 : currentIndex + 1;
    } else if (
      (orientation === "horizontal" && event.key === "ArrowLeft") ||
      (orientation === "vertical" && event.key === "ArrowUp")
    ) {
      nextIndex = currentIndex === 0 ? lastIndex : currentIndex - 1;
    }

    if (nextIndex === undefined) {
      return;
    }

    event.preventDefault();
    if (options[nextIndex].id !== active) {
      onChange(options[nextIndex].id);
    }
    event.currentTarget.parentElement
      ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
      [nextIndex]?.focus();
  };

  return (
    <div
      aria-label={label}
      aria-orientation={orientation}
      className="mode-tabs"
      data-layout="mode-tabs"
      id={tabListId}
      role="tablist"
    >
      {options.map((option, index) => {
        const isActive = active === option.id;
        return (
          <button
            aria-controls={option.controlsId}
            aria-selected={isActive}
            className={isActive ? "active" : ""}
            id={`${tabListId}-tab-${index}`}
            key={option.id}
            onKeyDown={(event) => handleKeyDown(event, index)}
            onClick={() => onChange(option.id)}
            role="tab"
            tabIndex={isActive || (!hasActiveOption && index === 0) ? 0 : -1}
            type="button"
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
