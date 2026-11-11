import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";

export function TopBar({ title, back = "/" }: { title: string; back?: string | null }) {
  const nav = useNavigate();
  return (
    <div className="topbar">
      {back !== null && (
        <button className="back" onClick={() => nav(back)} aria-label="Назад">
          ← Назад
        </button>
      )}
      <h1>{title}</h1>
    </div>
  );
}

export function Card({ children }: { children: ReactNode }) {
  return <div className="card stack">{children}</div>;
}
