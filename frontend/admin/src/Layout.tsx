import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import { useIsMobile } from "./components";
import { staffTitle } from "./helpers";

export interface NavItem {
  to: string;
  label: string;
  short: string;
  icon: string;
  adminOnly?: boolean;
  primary?: boolean;
}

export const NAV: NavItem[] = [
  { to: "/withdrawals", label: "Выводы", short: "Выводы", icon: "⇪", primary: true },
  { to: "/orders", label: "Заявки", short: "Заявки", icon: "₽", primary: true },
  { to: "/users", label: "Клиенты", short: "Клиенты", icon: "☺", primary: true },
  { to: "/antifraud", label: "Антифрод", short: "Антифрод", icon: "⚑", primary: true },
  { to: "/dashboard", label: "Дашборд", short: "Дашборд", icon: "▤", adminOnly: true },
  { to: "/settings", label: "Настройки", short: "Настройки", icon: "⚙", adminOnly: true },
  { to: "/staff", label: "Сотрудники", short: "Сотрудники", icon: "☷", adminOnly: true },
  { to: "/audit", label: "Аудит", short: "Аудит", icon: "☰" },
  { to: "/profile", label: "Профиль", short: "Профиль", icon: "◉" },
];

export function Layout() {
  const { me } = useAuth();
  const mobile = useIsMobile();
  const location = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);
  const items = NAV.filter((n) => !n.adminOnly || me.is_admin);

  useEffect(() => setMoreOpen(false), [location.pathname]);

  if (mobile) {
    const primary = items.filter((n) => n.primary);
    const rest = items.filter((n) => !n.primary);
    const restActive = rest.some((n) => location.pathname.startsWith(n.to));
    return (
      <div className="app app-mobile">
        <main className="main">
          <Outlet />
        </main>
        {moreOpen && (
          <div className="more-sheet" role="dialog" aria-label="Ещё разделы">
            <div className="card">
              {rest.map((n) => (
                <NavLink key={n.to} to={n.to} className={({ isActive }) => `more-item${isActive ? " active" : ""}`}>
                  <span aria-hidden>{n.icon}</span> {n.label}
                </NavLink>
              ))}
            </div>
          </div>
        )}
        <nav className="bottom-nav" aria-label="Разделы">
          {primary.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => `bottom-item${isActive ? " active" : ""}`}>
              <span className="bottom-icon" aria-hidden>
                {n.icon}
              </span>
              <span>{n.short}</span>
            </NavLink>
          ))}
          <button
            type="button"
            className={`bottom-item${restActive || moreOpen ? " active" : ""}`}
            onClick={() => setMoreOpen((v) => !v)}
            aria-expanded={moreOpen}
          >
            <span className="bottom-icon" aria-hidden>
              ⋯
            </span>
            <span>Ещё</span>
          </button>
        </nav>
      </div>
    );
  }

  return (
    <div className="app app-desktop">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <strong>Админка</strong>
          <div className="muted small">
            {staffTitle(me.staff)} · {me.is_admin ? "админ" : "оператор"}
          </div>
        </div>
        <nav aria-label="Разделы">
          {items.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => `side-item${isActive ? " active" : ""}`}>
              <span className="side-icon" aria-hidden>
                {n.icon}
              </span>
              {n.label}
            </NavLink>
          ))}
        </nav>
        {me.is_mock && <div className="badge badge-warn sidebar-mock">Платежи: mock</div>}
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
