import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { formatDateTime, formatRate, formatRub, formatUsdt } from "@shared/format";
import { StatusBadge } from "@shared/ui";
import { getPage } from "../api";
import { PageHeader, PagedView, ResponsiveList, usePaged, type Column } from "../components";
import type { Order } from "../types";

export const ORDER_STATUSES: { value: string; label: string }[] = [
  { value: "creating", label: "Создаётся" },
  { value: "pending", label: "Ожидает оплаты" },
  { value: "paid", label: "Оплачена" },
  { value: "review", label: "На проверке" },
  { value: "expired", label: "Истекла" },
  { value: "cancelled", label: "Отменена" },
  { value: "rejected", label: "Отклонена" },
  { value: "failed", label: "Ошибка создания" },
];

const FILTER_KEYS = ["status", "user_id", "from", "to", "q"] as const;
type Filters = Record<(typeof FILTER_KEYS)[number], string>;

export const orderColumns: Column<Order>[] = [
  {
    title: "Номер",
    render: (o) => (
      <span className="id-cell">
        <Link to={`/orders/${o.id}`} className="public-id">
          {o.public_id}
        </Link>
        <StatusBadge status={o.status} label={o.status_label} />
      </span>
    ),
  },
  { title: "Сумма", render: (o) => <b className="num">{formatRub(o.amount_rub)}</b> },
  { title: "USDT", render: (o) => formatUsdt(o.credited_usdt ?? o.usdt_amount) },
  { title: "Курс", render: (o) => formatRate(o.rate) },
  { title: "Клиент", render: (o) => <Link to={`/users/${o.user_id}`}>id {o.user_id}</Link> },
  { title: "Создана", render: (o) => formatDateTime(o.created_at) },
];

export function OrdersPage() {
  const [params, setParams] = useSearchParams();
  const applied: Filters = Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) ?? ""])) as Filters;
  const [draft, setDraft] = useState<Filters>(applied);

  const paged = usePaged<Order>(
    (cursor) => getPage<Order>("/orders", { ...applied }, cursor),
    FILTER_KEYS.map((k) => applied[k]),
  );

  function apply(next: Filters) {
    const p = new URLSearchParams();
    for (const k of FILTER_KEYS) if (next[k].trim()) p.set(k, next[k].trim());
    setParams(p, { replace: true });
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    apply(draft);
  }

  function reset() {
    const empty = { status: "", user_id: "", from: "", to: "", q: "" };
    setDraft(empty);
    apply(empty);
  }

  const set = (k: keyof Filters) => (v: string) => setDraft((d) => ({ ...d, [k]: v }));
  const anyFilter = FILTER_KEYS.some((k) => applied[k]);

  return (
    <div className="page">
      <PageHeader title="Заявки">
        <button className="btn btn-ghost btn-sm" onClick={() => void paged.reload()} disabled={paged.loading}>
          Обновить
        </button>
      </PageHeader>
      <form className="card filters-grid" onSubmit={submit}>
        <label className="field">
          <span>Статус</span>
          <select
            value={draft.status}
            onChange={(e) => {
              set("status")(e.target.value);
              apply({ ...draft, status: e.target.value });
            }}
          >
            <option value="">Все</option>
            {ORDER_STATUSES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>ID клиента</span>
          <input
            className="input"
            inputMode="numeric"
            value={draft.user_id}
            onChange={(e) => set("user_id")(e.target.value.replace(/\D/g, ""))}
          />
        </label>
        <label className="field">
          <span>С даты</span>
          <input className="input" type="date" value={draft.from} onChange={(e) => set("from")(e.target.value)} />
        </label>
        <label className="field">
          <span>По дату</span>
          <input className="input" type="date" value={draft.to} onChange={(e) => set("to")(e.target.value)} />
        </label>
        <label className="field">
          <span>Номер B-… или ID платежа</span>
          <input className="input" value={draft.q} maxLength={100} onChange={(e) => set("q")(e.target.value)} />
        </label>
        <div className="btn-row filters-actions">
          <button className="btn btn-primary" type="submit">
            Применить
          </button>
          {anyFilter && (
            <button className="btn btn-ghost" type="button" onClick={reset}>
              Сбросить
            </button>
          )}
        </div>
      </form>
      <PagedView paged={paged}>
        {(items) => (
          <ResponsiveList
            items={items}
            columns={orderColumns}
            rowKey={(o) => o.id}
            link={(o) => `/orders/${o.id}`}
            emptyText={anyFilter ? "Ничего не найдено." : "Заявок пока нет."}
          />
        )}
      </PagedView>
    </div>
  );
}
