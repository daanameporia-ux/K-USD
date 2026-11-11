import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { formatDateTime, formatUsdt } from "@shared/format";
import { getPage } from "../api";
import { PageHeader, PagedView, ResponsiveList, usePaged, type Column } from "../components";
import { userTitle } from "../helpers";
import type { User } from "../types";

const FILTERS = [
  { id: "", label: "Все" },
  { id: "suspicious", label: "Подозрительные" },
  { id: "af_blocked", label: "Заблокированы антифродом" },
  { id: "blocked", label: "Заблокированы" },
];

export const USER_STATUS: Record<string, string> = {
  registering: "регистрация",
  active: "активен",
  blocked: "заблокирован",
};

export function UserFlags({ u }: { u: User }) {
  return (
    <span className="flags">
      {u.status === "blocked" && <span className="badge badge-bad">заблокирован</span>}
      {!u.can_create_orders && <span className="badge badge-bad">заявки запрещены</span>}
      {u.is_suspicious && <span className="badge badge-warn">подозрительный</span>}
      {!u.af_enabled && <span className="badge badge-muted">антифрод выкл.</span>}
      {u.bot_blocked && <span className="badge badge-muted">бот заблокирован</span>}
    </span>
  );
}

function fullName(u: User): string {
  return [u.first_name, u.last_name].filter(Boolean).join(" ") || "—";
}

export function UsersPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const filter = params.get("filter") ?? "";
  const [qDraft, setQDraft] = useState(q);

  const paged = usePaged<User>((cursor) => getPage<User>("/users", { q, filter }, cursor), [q, filter]);

  function update(next: Record<string, string>) {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      if (v) p.set(k, v);
      else p.delete(k);
    }
    setParams(p, { replace: true });
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    update({ q: qDraft.trim() });
  }

  const columns: Column<User>[] = [
    {
      title: "Клиент",
      render: (u) => (
        <span className="id-cell">
          <Link to={`/users/${u.id}`}>
            <b>{userTitle(u)}</b>
          </Link>
          <span className="muted small">id {u.id}</span>
        </span>
      ),
    },
    { title: "Имя", render: fullName },
    { title: "Статус", render: (u) => USER_STATUS[u.status] ?? u.status },
    { title: "Баланс", render: (u) => <span className="num">{formatUsdt(u.balance)}</span> },
    { title: "В холде", render: (u) => <span className="num">{formatUsdt(u.hold)}</span> },
    { title: "Флаги", render: (u) => <UserFlags u={u} /> },
    { title: "Был в сети", render: (u) => formatDateTime(u.last_seen_at) },
  ];

  return (
    <div className="page">
      <PageHeader title="Клиенты" />
      <form className="filters" onSubmit={submit}>
        <input
          className="input"
          placeholder="ID, Telegram ID, @username, B-… или W-…"
          value={qDraft}
          maxLength={100}
          onChange={(e) => setQDraft(e.target.value)}
        />
        <button className="btn btn-secondary" type="submit">
          Найти
        </button>
        {q && (
          <button
            className="btn btn-ghost"
            type="button"
            onClick={() => {
              setQDraft("");
              update({ q: "" });
            }}
          >
            Сбросить
          </button>
        )}
      </form>
      <div className="chips">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            className={`chip${filter === f.id ? " chip-active" : ""}`}
            onClick={() => update({ filter: f.id })}
          >
            {f.label}
          </button>
        ))}
      </div>
      <PagedView paged={paged}>
        {(items) => (
          <ResponsiveList
            items={items}
            columns={columns}
            rowKey={(u) => u.id}
            link={(u) => `/users/${u.id}`}
            emptyText={q || filter ? "Никого не найдено." : "Клиентов пока нет."}
          />
        )}
      </PagedView>
    </div>
  );
}
