import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { formatDateTime, formatRub, formatUsdt, shortAddress } from "@shared/format";
import { confirmAction } from "@shared/telegram";
import { AsyncButton, ErrorView, Loader, Notice, Row, StatusBadge, useLoad } from "@shared/ui";
import { adminPath, api, getPage } from "../api";
import { useMe } from "../auth";
import {
  Check,
  PageHeader,
  PagedView,
  ResponsiveList,
  Section,
  TextField,
  useAction,
  usePaged,
  type Column,
} from "../components";
import { formatDuration, formatPct, parsePositiveInt, userTitle, validateSignedAmount } from "../helpers";
import type { ClientStats, DayStats, LedgerEntry, Order, User, Withdrawal } from "../types";
import { afColumns } from "./Antifraud";
import { orderColumns } from "./Orders";
import { USER_STATUS, UserFlags } from "./Users";

type Tab = "overview" | "daily" | "orders" | "withdrawals" | "ledger" | "antifraud";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Обзор" },
  { id: "daily", label: "По дням" },
  { id: "orders", label: "Заявки" },
  { id: "withdrawals", label: "Выводы" },
  { id: "ledger", label: "Проводки" },
  { id: "antifraud", label: "Антифрод" },
];

const LEDGER_TYPES: Record<string, string> = {
  deposit: "Зачисление",
  withdrawal_hold: "Холд вывода",
  withdrawal_release: "Возврат холда",
  withdrawal_complete: "Вывод исполнен",
  adjustment: "Корректировка",
};

export function UserDetailPage() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.id === params.get("tab"))?.id ?? "overview") as Tab;
  const { data: user, error, loading, reload } = useLoad(() => api.get<User>(adminPath(`/users/${id}`)), [id]);

  if (loading && !user) return <Loader />;
  if (error && !user) return <ErrorView error={error} onRetry={() => void reload()} />;
  if (!user) return null;

  return (
    <div className="page">
      <PageHeader title={userTitle(user)} back="/users">
        <UserFlags u={user} />
      </PageHeader>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={t.id === tab}
            className={`tab${t.id === tab ? " tab-active" : ""}`}
            onClick={() => setParams(t.id === "overview" ? {} : { tab: t.id }, { replace: true })}
          >
            {t.label}
            {t.id === "antifraud" && user.antifraud_events?.some((e) => !e.resolved_at) ? " •" : ""}
          </button>
        ))}
      </div>
      {tab === "overview" && <Overview user={user} onChanged={() => reload(true)} />}
      {tab === "daily" && <Daily userId={user.id} />}
      {tab === "orders" && <UserOrders userId={user.id} />}
      {tab === "withdrawals" && <UserWithdrawals userId={user.id} />}
      {tab === "ledger" && <Ledger userId={user.id} />}
      {tab === "antifraud" && (
        <ResponsiveList
          items={user.antifraud_events ?? []}
          columns={afColumns({ withUser: false })}
          rowKey={(e) => e.id}
          emptyText="Событий антифрода нет."
        />
      )}
    </div>
  );
}

function Overview({ user, onChanged }: { user: User; onChanged: () => Promise<void> }) {
  const me = useMe();
  return (
    <>
      <div className="detail-grid">
        <Section title="Профиль">
          <Row label="ID">{user.id}</Row>
          <Row label="Telegram ID">{user.tg_id ?? "—"}</Row>
          <Row label="Username">{user.tg_username ? "@" + user.tg_username : "—"}</Row>
          <Row label="Имя">{[user.first_name, user.last_name].filter(Boolean).join(" ") || "—"}</Row>
          <Row label="Статус">{USER_STATUS[user.status] ?? user.status}</Row>
          <Row label="KYC">{user.kyc_status}</Row>
          <Row label="Наценка">{formatPct(user.markup_pct)}</Row>
          <Row label="Лимит заявки">
            {user.order_min_rub || user.order_max_rub
              ? `${user.order_min_rub ? formatRub(user.order_min_rub) : "общий"} – ${user.order_max_rub ? formatRub(user.order_max_rub) : "общий"}`
              : "общие"}
          </Row>
          <Row label="Лимит в сутки">
            {user.daily_turnover_limit_rub ? formatRub(user.daily_turnover_limit_rub) : "общий"}
          </Row>
          <Row label="Антифрод">{user.af_enabled ? "включён" : "выключен"}</Row>
          <Row label="Создание заявок">{user.can_create_orders ? "разрешено" : "запрещено"}</Row>
          {user.orders_block_reason && <Row label="Причина блокировки">{user.orders_block_reason}</Row>}
          <Row label="Сброс антифрода">{formatDateTime(user.af_reset_at)}</Row>
          <Row label="Создан">{formatDateTime(user.created_at)}</Row>
          <Row label="Регистрация">{formatDateTime(user.registered_at)}</Row>
          <Row label="Был в сети">{formatDateTime(user.last_seen_at)}</Row>
        </Section>
        <div>
          <Section title="Балансы">
            <Row label="Баланс">
              <b>{formatUsdt(user.balance)}</b>
            </Row>
            <Row label="В холде">{formatUsdt(user.hold)}</Row>
            <Row label="Доступно к выводу">{formatUsdt(user.available_for_withdrawal)}</Row>
          </Section>
          <Stats userId={user.id} />
        </div>
      </div>
      {me.is_admin && <AdminActions user={user} onChanged={onChanged} />}
    </>
  );
}

function Stats({ userId }: { userId: number }) {
  const { data: s, error, loading, reload } = useLoad(
    () => api.get<ClientStats>(adminPath(`/users/${userId}/stats`)),
    [userId],
  );
  return (
    <Section title="Статистика">
      {loading && !s ? (
        <Loader />
      ) : error && !s ? (
        <ErrorView error={error} onRetry={() => void reload()} />
      ) : s ? (
        <>
          <Row label="Заявок создано / оплачено">
            {s.counts.created} / {s.counts.paid}
          </Row>
          <Row label="Истекло / отменено / отклонено">
            {s.counts.expired} / {s.counts.cancelled} / {s.counts.rejected}
          </Row>
          <Row label="На разборе">{s.counts.review}</Row>
          <Row label="Конверсия общая">{formatPct(s.conversion_pct)}</Row>
          <Row label={`Конверсия в окне (${s.window_finished} из ${s.window_size})`}>
            {formatPct(s.window_conversion_pct)}
          </Row>
          <Row label="Неоплат подряд">{s.unpaid_streak}</Row>
          <Row label="Оборот">{formatRub(s.turnover_rub)}</Row>
          <Row label="Получено USDT">{formatUsdt(s.usdt_received)}</Row>
          <Row label="Маржа">{formatRub(s.margin_rub)}</Row>
          <Row label="Средний чек">{formatRub(s.avg_check_rub)}</Row>
          <Row label="Медиана до оплаты">{formatDuration(s.median_pay_seconds)}</Row>
          <Row label="Выводов">{s.withdrawals_count}</Row>
          <Row label="Сумма выводов">{formatUsdt(s.withdrawals_sum)}</Row>
          <Row label="Комиссии выводов">{formatUsdt(s.withdrawals_fees)}</Row>
          <Row label="Первая / последняя сделка">
            {formatDateTime(s.first_deal_at)} / {formatDateTime(s.last_deal_at)}
          </Row>
        </>
      ) : null}
    </Section>
  );
}

export const dayColumns: Column<DayStats>[] = [
  { title: "День", render: (d) => <b>{d.day}</b> },
  { title: "Создано", render: (d) => d.created, className: "num" },
  { title: "Оплачено", render: (d) => d.paid, className: "num" },
  { title: "Завершено", render: (d) => d.finished, className: "num" },
  { title: "Конверсия", render: (d) => formatPct(d.conversion_pct), className: "num" },
  { title: "Оборот", render: (d) => formatRub(d.turnover_rub), className: "num" },
  { title: "USDT", render: (d) => formatUsdt(d.usdt), className: "num" },
  { title: "Маржа", render: (d) => formatRub(d.margin_rub), className: "num" },
];

function Daily({ userId }: { userId: number }) {
  const { data, error, loading, reload } = useLoad(
    () => api.get<{ items: DayStats[] }>(adminPath(`/users/${userId}/stats/daily`)),
    [userId],
  );
  if (loading && !data) return <Loader />;
  if (error && !data) return <ErrorView error={error} onRetry={() => void reload()} />;
  return (
    <ResponsiveList items={data?.items ?? []} columns={dayColumns} rowKey={(d) => d.day} emptyText="Данных по дням нет." />
  );
}

function UserOrders({ userId }: { userId: number }) {
  const paged = usePaged<Order>((cursor) => getPage<Order>("/orders", { user_id: userId }, cursor), [userId]);
  return (
    <PagedView paged={paged}>
      {(items) => (
        <ResponsiveList
          items={items}
          columns={orderColumns.filter((c) => c.title !== "Клиент")}
          rowKey={(o) => o.id}
          link={(o) => `/orders/${o.id}`}
          emptyText="Заявок нет."
        />
      )}
    </PagedView>
  );
}

const withdrawalColumns: Column<Withdrawal>[] = [
  {
    title: "Номер",
    render: (w) => (
      <span className="id-cell">
        <Link to={`/withdrawals/${w.id}`} className="public-id">
          {w.public_id}
        </Link>
        <StatusBadge status={w.status} label={w.status_label} />
      </span>
    ),
  },
  { title: "Сумма", render: (w) => formatUsdt(w.amount) },
  { title: "К отправке", render: (w) => formatUsdt(w.amount_to_send) },
  { title: "Сеть", render: (w) => w.network_label },
  { title: "Адрес", render: (w) => <span className="mono">{shortAddress(w.address)}</span> },
  { title: "Создан", render: (w) => formatDateTime(w.created_at) },
];

function UserWithdrawals({ userId }: { userId: number }) {
  const paged = usePaged<Withdrawal>(
    (cursor) => getPage<Withdrawal>("/withdrawals", { user_id: userId }, cursor),
    [userId],
  );
  return (
    <PagedView paged={paged}>
      {(items) => (
        <ResponsiveList
          items={items}
          columns={withdrawalColumns}
          rowKey={(w) => w.id}
          link={(w) => `/withdrawals/${w.id}`}
          emptyText="Выводов нет."
        />
      )}
    </PagedView>
  );
}

function signed(value: string | null): string {
  if (value === null || value === "0") return "—";
  return value.startsWith("-") ? value : "+" + value;
}

const ledgerColumns: Column<LedgerEntry>[] = [
  { title: "Тип", render: (e) => <b>{LEDGER_TYPES[e.type] ?? e.type}</b> },
  { title: "Баланс Δ", render: (e) => <span className="num">{signed(e.balance_delta)}</span>, className: "num" },
  { title: "Холд Δ", render: (e) => <span className="num">{signed(e.hold_delta)}</span>, className: "num" },
  { title: "Баланс после", render: (e) => e.balance_after ?? "—", className: "num" },
  { title: "Холд после", render: (e) => e.hold_after ?? "—", className: "num" },
  {
    title: "Основание",
    render: (e) =>
      e.order_id ? (
        <Link to={`/orders/${e.order_id}`}>заявка #{e.order_id}</Link>
      ) : e.withdrawal_id ? (
        <Link to={`/withdrawals/${e.withdrawal_id}`}>вывод #{e.withdrawal_id}</Link>
      ) : (
        e.comment ?? "—"
      ),
  },
  { title: "Когда", render: (e) => formatDateTime(e.created_at) },
];

function Ledger({ userId }: { userId: number }) {
  const paged = usePaged<LedgerEntry>(
    (cursor) => getPage<LedgerEntry>(`/users/${userId}/ledger`, {}, cursor),
    [userId],
  );
  return (
    <PagedView paged={paged}>
      {(items) => <ResponsiveList items={items} columns={ledgerColumns} rowKey={(e) => e.id} emptyText="Проводок нет." />}
    </PagedView>
  );
}

// --- admin actions --------------------------------------------------------------------------

function AdminActions({ user, onChanged }: { user: User; onChanged: () => Promise<void> }) {
  return (
    <div className="detail-grid">
      <EditUser user={user} onChanged={onChanged} />
      <div>
        <OrdersBlock user={user} onChanged={onChanged} />
        <AdjustBalance user={user} onChanged={onChanged} />
      </div>
    </div>
  );
}

function EditUser({ user, onChanged }: { user: User; onChanged: () => Promise<void> }) {
  const initial = () => ({
    markup_pct: user.markup_pct ?? "",
    order_min_rub: user.order_min_rub ? String(user.order_min_rub) : "",
    order_max_rub: user.order_max_rub ? String(user.order_max_rub) : "",
    daily_turnover_limit_rub: user.daily_turnover_limit_rub ? String(user.daily_turnover_limit_rub) : "",
    af_enabled: user.af_enabled,
    is_suspicious: user.is_suspicious,
    blocked: user.status === "blocked",
  });
  const [f, setF] = useState(initial);
  const [clearLimits, setClearLimits] = useState(false);
  const [comment, setComment] = useState("");
  const action = useAction();

  const limitKeys = ["order_min_rub", "order_max_rub", "daily_turnover_limit_rub"] as const;

  function buildPatch(): Record<string, unknown> | string {
    const body: Record<string, unknown> = {};
    const init = initial();
    const markup = f.markup_pct.trim().replace(",", ".");
    if (markup !== init.markup_pct) {
      if (!/^\d{1,2}(\.\d{1,2})?$/.test(markup)) return "Наценка — число от 0 до 99.99.";
      body.markup_pct = markup;
    }
    if (clearLimits) body.clear_limits = true;
    else
      for (const k of limitKeys) {
        if (f[k].trim() === init[k]) continue;
        const v = parsePositiveInt(f[k]);
        if (v === "invalid") return "Лимиты — целые положительные числа (рубли).";
        if (v !== null) body[k] = v;
        else return "Чтобы убрать индивидуальные лимиты, отметьте «Сбросить индивидуальные лимиты».";
      }
    if (f.af_enabled !== init.af_enabled) body.af_enabled = f.af_enabled;
    if (f.is_suspicious !== init.is_suspicious) body.is_suspicious = f.is_suspicious;
    if (f.blocked !== init.blocked) body.status = f.blocked ? "blocked" : "active";
    if (Object.keys(body).length === 0) return "Нет изменений.";
    if (comment.trim()) body.comment = comment.trim();
    return body;
  }

  async function save() {
    const body = buildPatch();
    if (typeof body === "string") {
      action.setError(body);
      return;
    }
    if ("status" in body) {
      const text = body.status === "blocked" ? "Заблокировать клиента?" : "Разблокировать клиента?";
      if (!(await confirmAction(text))) return;
    }
    const ok = await action.run(() => api.patch<User>(adminPath(`/users/${user.id}`), body), "Сохранено.");
    if (ok) {
      setClearLimits(false);
      setComment("");
      await onChanged();
    }
  }

  // Re-sync the form when the user object changes after save.
  const [syncedFor, setSyncedFor] = useState(user);
  if (syncedFor !== user) {
    setSyncedFor(user);
    setF(initial());
  }

  return (
    <Section title="Параметры клиента">
      <div className="stack">
        <TextField
          label="Наценка, %"
          value={f.markup_pct}
          inputMode="decimal"
          onChange={(v) => setF({ ...f, markup_pct: v })}
        />
        <div className="grid2">
          <TextField
            label="Мин. заявка, ₽"
            value={f.order_min_rub}
            inputMode="numeric"
            placeholder="общий"
            onChange={(v) => setF({ ...f, order_min_rub: v })}
          />
          <TextField
            label="Макс. заявка, ₽"
            value={f.order_max_rub}
            inputMode="numeric"
            placeholder="общий"
            onChange={(v) => setF({ ...f, order_max_rub: v })}
          />
        </div>
        <TextField
          label="Лимит оборота в сутки, ₽ (B10)"
          value={f.daily_turnover_limit_rub}
          inputMode="numeric"
          placeholder="общий"
          onChange={(v) => setF({ ...f, daily_turnover_limit_rub: v })}
        />
        <Check label="Сбросить индивидуальные лимиты" checked={clearLimits} onChange={setClearLimits} />
        <Check label="Антифрод включён для клиента" checked={f.af_enabled} onChange={(v) => setF({ ...f, af_enabled: v })} />
        <Check label="Подозрительный" checked={f.is_suspicious} onChange={(v) => setF({ ...f, is_suspicious: v })} />
        <Check label="Заблокирован" checked={f.blocked} onChange={(v) => setF({ ...f, blocked: v })} />
        <TextField label="Комментарий для аудита" value={comment} onChange={setComment} maxLength={1000} />
        {action.view}
        <AsyncButton onClick={save}>Сохранить</AsyncButton>
      </div>
    </Section>
  );
}

function OrdersBlock({ user, onChanged }: { user: User; onChanged: () => Promise<void> }) {
  const [comment, setComment] = useState("");
  const action = useAction();
  const blocked = !user.can_create_orders;

  async function submit(kind: "unblock" | "block") {
    const c = comment.trim();
    if (!c) {
      action.setError("Укажите комментарий.");
      return;
    }
    const text =
      kind === "unblock"
        ? "Разблокировать создание заявок? Флаг «подозрительный» будет снят, счётчики антифрода сброшены."
        : "Запретить клиенту создавать заявки?";
    if (!(await confirmAction(text))) return;
    const ok = await action.run(
      () => api.post<User>(adminPath(`/users/${user.id}/${kind}-orders`), { comment: c }),
      kind === "unblock" ? "Создание заявок разрешено." : "Создание заявок запрещено.",
    );
    if (ok) {
      setComment("");
      await onChanged();
    }
  }

  return (
    <Section title="Создание заявок">
      <div className="stack">
        <p className="muted small">
          {blocked
            ? "Сейчас клиенту запрещено создавать заявки. Разблокировка снимает «подозрительный» и сбрасывает окно антифрода."
            : user.is_suspicious
              ? "Заявки разрешены, клиент помечен подозрительным. Разблокировка снимет флаг и сбросит окно антифрода."
              : "Заявки разрешены."}
        </p>
        <TextField label="Комментарий (обязательно)" value={comment} onChange={setComment} maxLength={1000} />
        {action.view}
        <div className="btn-row">
          {(blocked || user.is_suspicious) && (
            <AsyncButton onClick={() => submit("unblock")} disabled={!comment.trim()}>
              Разблокировать заявки
            </AsyncButton>
          )}
          {!blocked && (
            <AsyncButton variant="danger" onClick={() => submit("block")} disabled={!comment.trim()}>
              Запретить заявки
            </AsyncButton>
          )}
        </div>
      </div>
    </Section>
  );
}

function AdjustBalance({ user, onChanged }: { user: User; onChanged: () => Promise<void> }) {
  const [amount, setAmount] = useState("");
  const [comment, setComment] = useState("");
  const action = useAction();
  const amountError = amount.trim() ? validateSignedAmount(amount) : null;
  const commentOk = comment.trim().length >= 10;

  async function submit() {
    const err = validateSignedAmount(amount);
    if (err) {
      action.setError(err);
      return;
    }
    if (!commentOk) {
      action.setError("Комментарий — минимум 10 символов.");
      return;
    }
    const value = amount.trim().replace(",", ".");
    if (!(await confirmAction(`Изменить баланс клиента на ${value} USDT?`))) return;
    const ok = await action.run(
      () => api.post(adminPath(`/users/${user.id}/adjust-balance`), { amount: value, comment: comment.trim() }),
      "Баланс скорректирован.",
    );
    if (ok) {
      setAmount("");
      setComment("");
      await onChanged();
    }
  }

  return (
    <Section title="Корректировка баланса">
      <div className="stack">
        <Notice tone="warn">Ручная проводка. Итоговый баланс не может стать отрицательным. В служебный чат уйдёт алерт.</Notice>
        <TextField
          label="Сумма со знаком, USDT (например -10.50 или 25)"
          value={amount}
          onChange={setAmount}
          inputMode="text"
          maxLength={40}
        />
        {amountError && <div className="field-error">{amountError}</div>}
        <label className="field">
          <span>Комментарий (минимум 10 символов)</span>
          <textarea rows={2} value={comment} maxLength={1000} onChange={(e) => setComment(e.target.value)} />
        </label>
        {action.view}
        <AsyncButton variant="danger" onClick={submit} disabled={!amount.trim() || !!amountError || !commentOk}>
          Провести корректировку
        </AsyncButton>
      </div>
    </Section>
  );
}

