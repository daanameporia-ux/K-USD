import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { formatDateTime } from "@shared/format";
import { getPage } from "../api";
import { useMe } from "../auth";
import { PageHeader, PagedView, ResponsiveList, usePaged, type Column } from "../components";
import { compactJson } from "../helpers";
import type { AuditEntry } from "../types";

const ENTITY_TYPES = ["withdrawal", "order", "user", "setting", "staff", "blacklist", "rate", "antifraud_event"];

const KEYS = ["staff_id", "entity_type", "entity_id"] as const;
type Filters = Record<(typeof KEYS)[number], string>;

function entityLink(a: AuditEntry) {
  const label = `${a.entity_type ?? "—"}${a.entity_id ? " #" + a.entity_id : ""}`;
  if (!a.entity_id) return label;
  const to =
    a.entity_type === "withdrawal"
      ? `/withdrawals/${a.entity_id}`
      : a.entity_type === "order"
        ? `/orders/${a.entity_id}`
        : a.entity_type === "user"
          ? `/users/${a.entity_id}`
          : null;
  return to ? <Link to={to}>{label}</Link> : label;
}

export function AuditPage() {
  const me = useMe();
  const [params, setParams] = useSearchParams();
  const applied = Object.fromEntries(KEYS.map((k) => [k, params.get(k) ?? ""])) as Filters;
  const [draft, setDraft] = useState<Filters>(applied);

  const paged = usePaged<AuditEntry>(
    (cursor) => getPage<AuditEntry>("/audit", { ...applied }, cursor),
    KEYS.map((k) => applied[k]),
  );

  function submit(e: FormEvent) {
    e.preventDefault();
    const p = new URLSearchParams();
    for (const k of KEYS) if (draft[k].trim()) p.set(k, draft[k].trim());
    setParams(p, { replace: true });
  }

  const columns: Column<AuditEntry>[] = [
    {
      title: "Действие",
      render: (a) => (
        <span className="id-cell">
          <b className="mono">{a.action}</b>
          <span className="muted small">{formatDateTime(a.created_at)}</span>
        </span>
      ),
    },
    { title: "Сотрудник", render: (a) => (a.staff_id ? `#${a.staff_id}` : "система") },
    { title: "Объект", render: entityLink },
    { title: "До", render: (a) => <code className="json">{compactJson(a.before)}</code> },
    { title: "После", render: (a) => <code className="json">{compactJson(a.after)}</code> },
    { title: "Комментарий", render: (a) => a.comment ?? "—" },
    { title: "IP", render: (a) => a.ip ?? "—", desktopOnly: true },
  ];

  const anyFilter = KEYS.some((k) => applied[k]);

  return (
    <div className="page">
      <PageHeader title="Аудит" />
      {!me.is_admin && <p className="muted small">Показаны только ваши действия.</p>}
      <form className="card filters-grid" onSubmit={submit}>
        {me.is_admin && (
          <label className="field">
            <span>ID сотрудника</span>
            <input
              className="input"
              inputMode="numeric"
              value={draft.staff_id}
              onChange={(e) => setDraft({ ...draft, staff_id: e.target.value.replace(/\D/g, "") })}
            />
          </label>
        )}
        <label className="field">
          <span>Тип объекта</span>
          <input
            className="input"
            list="audit-entity-types"
            value={draft.entity_type}
            maxLength={50}
            onChange={(e) => setDraft({ ...draft, entity_type: e.target.value })}
          />
          <datalist id="audit-entity-types">
            {ENTITY_TYPES.map((t) => (
              <option key={t} value={t} />
            ))}
          </datalist>
        </label>
        <label className="field">
          <span>ID объекта</span>
          <input
            className="input"
            value={draft.entity_id}
            maxLength={50}
            onChange={(e) => setDraft({ ...draft, entity_id: e.target.value })}
          />
        </label>
        <div className="btn-row filters-actions">
          <button className="btn btn-primary" type="submit">
            Применить
          </button>
          {anyFilter && (
            <button
              className="btn btn-ghost"
              type="button"
              onClick={() => {
                setDraft({ staff_id: "", entity_type: "", entity_id: "" });
                setParams({}, { replace: true });
              }}
            >
              Сбросить
            </button>
          )}
        </div>
      </form>
      <PagedView paged={paged}>
        {(items) => (
          <ResponsiveList
            items={items}
            columns={columns}
            rowKey={(a) => a.id}
            emptyText={anyFilter ? "Ничего не найдено." : "Записей пока нет."}
          />
        )}
      </PagedView>
    </div>
  );
}
