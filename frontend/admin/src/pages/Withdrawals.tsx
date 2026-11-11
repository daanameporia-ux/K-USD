import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { formatDateTime, formatUsdt, shortAddress } from "@shared/format";
import { StatusBadge } from "@shared/ui";
import { getPage } from "../api";
import { useMe } from "../auth";
import { PageHeader, PagedView, ResponsiveList, usePaged, type Column } from "../components";
import { WITHDRAWAL_TABS, isAdminOnlyAmount, isWithdrawalTab, userTitle, withdrawalTabQuery, type WithdrawalTab } from "../helpers";
import type { Withdrawal } from "../types";

const EMPTY: Record<WithdrawalTab, string> = {
  queue: "Очередь пуста.",
  mine: "У вас нет выводов в работе.",
  verifying: "Нет выводов на проверке.",
  done: "Завершённых выводов нет.",
};

export function WithdrawalsPage() {
  const me = useMe();
  const [params, setParams] = useSearchParams();
  const tabParam = params.get("tab");
  const tab: WithdrawalTab = isWithdrawalTab(tabParam) ? tabParam : "queue";
  const network = params.get("network") ?? "";
  const q = params.get("q") ?? "";
  const [qDraft, setQDraft] = useState(q);

  const paged = usePaged<Withdrawal>(
    (cursor) => getPage<Withdrawal>("/withdrawals", { ...withdrawalTabQuery(tab), network, q }, cursor),
    [tab, network, q],
  );

  function update(next: Record<string, string>) {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      if (v) p.set(k, v);
      else p.delete(k);
    }
    setParams(p, { replace: true });
  }

  function submitSearch(e: FormEvent) {
    e.preventDefault();
    update({ q: qDraft.trim() });
  }

  const columns: Column<Withdrawal>[] = [
    {
      title: "Номер",
      render: (w) => (
        <span className="id-cell">
          <Link to={`/withdrawals/${w.id}`} className="public-id">
            {w.public_id}
          </Link>
          <StatusBadge status={w.status} label={w.status_label} />
          {isAdminOnlyAmount(w.amount, me.withdraw_admin_only_from_usdt, me.b6_enabled) &&
            !["completed", "rejected", "cancelled"].includes(w.status) && (
              <span className="badge badge-warn">только админ</span>
            )}
        </span>
      ),
    },
    { title: "Клиент", render: (w) => userTitle(w.user) },
    { title: "Сеть", render: (w) => w.network_label },
    { title: "К отправке", render: (w) => <b className="num">{formatUsdt(w.amount_to_send)}</b>, className: "num" },
    { title: "Адрес", render: (w) => <span className="mono">{shortAddress(w.address)}</span> },
    { title: "Создан", render: (w) => formatDateTime(w.created_at) },
    ...(tab === "verifying" || tab === "mine"
      ? [{ title: "Проверка", render: (w: Withdrawal) => w.verify_label ?? "—" }]
      : []),
  ];

  return (
    <div className="page">
      <PageHeader title="Выводы">
        <button className="btn btn-ghost btn-sm" onClick={() => void paged.reload()} disabled={paged.loading}>
          Обновить
        </button>
      </PageHeader>
      <div className="tabs" role="tablist">
        {WITHDRAWAL_TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={t.id === tab}
            className={`tab${t.id === tab ? " tab-active" : ""}`}
            onClick={() => update({ tab: t.id === "queue" ? "" : t.id })}
          >
            {t.label}
          </button>
        ))}
      </div>
      <form className="filters" onSubmit={submitSearch}>
        <select value={network} onChange={(e) => update({ network: e.target.value })} aria-label="Сеть">
          <option value="">Все сети</option>
          <option value="TRC20">TRC-20</option>
          <option value="BEP20">BEP-20</option>
        </select>
        <input
          className="input"
          placeholder="W-номер, адрес или хэш"
          value={qDraft}
          onChange={(e) => setQDraft(e.target.value)}
          maxLength={200}
        />
        <button className="btn btn-secondary" type="submit">
          Найти
        </button>
      </form>
      <PagedView paged={paged}>
        {(items) => (
          <ResponsiveList
            items={items}
            columns={columns}
            rowKey={(w) => w.id}
            link={(w) => `/withdrawals/${w.id}`}
            emptyText={q || network ? "Ничего не найдено." : EMPTY[tab]}
          />
        )}
      </PagedView>
    </div>
  );
}
