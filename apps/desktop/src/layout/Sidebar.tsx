import type { DockStartProject } from "../types";
import { navigationItems, resolveNavigationTarget, type PageId } from "../navigation/pages";

type SidebarProps = {
  currentPage: PageId;
  project: DockStartProject | null;
  onNavigate: (page: PageId) => void;
};

export default function Sidebar({ currentPage, project, onNavigate }: SidebarProps) {
  const hasProject = Boolean(project);

  return (
    <aside className="app-sidebar" aria-label="DockStart 主导航">
      <div className="sidebar-brand">
        <strong>DockStart</strong>
        <span>中文分子对接工作台</span>
      </div>
      <nav className="sidebar-nav">
        {navigationItems.map((item) => {
          const target = resolveNavigationTarget(item, hasProject);
          const active = currentPage === item.id || currentPage === target;
          const disabled = item.disabled;
          return (
            <button
              className={active ? "sidebar-nav-item active" : "sidebar-nav-item"}
              disabled={disabled}
              key={item.id}
              onClick={() => onNavigate(target)}
              type="button"
            >
              <span>{item.label}</span>
              <small>{item.requiresProject && !hasProject ? "需先创建项目" : item.description}</small>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
