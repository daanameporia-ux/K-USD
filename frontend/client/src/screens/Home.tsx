import { Navigate, useNavigate } from "react-router-dom";
import { formatDateTime, formatRate, formatRub, formatUsdt, statusTone } from "@shared/format";
import { ErrorView, Loader, Notice, useLoad } from "@shared/ui";
import { api, type HistoryItem, type Me, type Rate } from "../api";
import { Bubble, Icon, Money } from "./common";

export function Home() {
  const nav = useNavigate();
  const me = useLoad(() => api.get<Me>("/api/client/me"));
  const rate = useLoad(() => api.get<Rate>("/api/client/rate"));
  const recent = useLoad(() => api.get<{ items: HistoryItem[] }>("/api/client/history?limit=3"));

  if (me.loading) return <Loader />;
  if (me.error || !me.data) return <ErrorView error={me.error} onRetry={() => me.reload()} />;
  const m = me.data;
  if (!m.registered && !m.blocked) return <Navigate to="/register" replace />;
  const items = recent.data?.items ?? [];

  return (
    <div className="screen">
      <div className="home-head">
        <div className="brand">
          <span className="brand-mark">
            <Icon name="bolt" size={18} />
          </span>
          K-USD
        </div>
        <span className="pill" title="Поддержка">
          <Icon name="chat" size={16} />
          {m.support_contact}
        </span>
      </div>

      {m.blocked && <Notice tone="bad">Ваш аккаунт заблокирован. Обратитесь в поддержку: {m.support_contact}</Notice>}

      <div className="hero">
        <div className="hero-label">Баланс</div>
        <div className="balance num" data-testid="balance">
          <Money value={formatUsdt(m.balance)} />
        </div>
        {m.hold !== "0.00" && <div className="hero-sub">В обработке: {formatUsdt(m.hold)}</div>}
        <div className="hero-chip">
          {rate.data ? (
            <>1 USDT = {formatRate(rate.data.effective_rate)}</>
          ) : rate.loading ? (
            "Загрузка курса…"
          ) : (
            "Курс временно недоступен"
          )}
        </div>
      </div>

      <div className="actions">
        <button className="action action-main" onClick={() => nav("/buy")} disabled={m.blocked}>
          <span className="action-icon">
            <Icon name="plus" size={24} />
          </span>
          Купить
        </button>
        <button className="action" onClick={() => nav("/withdraw")} disabled={m.blocked}>
          <span className="action-icon">
            <Icon name="up" size={24} />
          </span>
          Вывести
        </button>
        <button className="action" onClick={() => nav("/history")}>
          <span className="action-icon">
            <Icon name="clock" size={24} />
          </span>
          История
        </button>
      </div>

      {m.proof_pending_order && (
        <div className="tile-card">
          <button className="tile" onClick={() => nav(`/order/${m.proof_pending_order!.id}`)}>
            <Bubble icon="paste" tone="warn" />
            <span className="tile-body">
              <span className="tile-title">Загрузите скриншот оплаты</span>
              <div className="tile-sub">Заявка {m.proof_pending_order.public_id} · без него нельзя создать новую</div>
            </span>
            <span className="tile-chevron">
              <Icon name="chevron" size={18} />
            </span>
          </button>
        </div>
      )}

      {m.active_order && (
        <div className="tile-card">
          <button className="tile" onClick={() => nav(`/order/${m.active_order!.id}`)}>
            <Bubble icon="qr" tone="warn" />
            <span className="tile-body">
              <span className="tile-title">Заявка {m.active_order.public_id}</span>
              <div className="tile-sub">
                {m.active_order.status_label} · {formatRub(m.active_order.amount_rub)}
              </div>
            </span>
            <span className="tile-chevron">
              <Icon name="chevron" size={18} />
            </span>
          </button>
        </div>
      )}
      {!m.can_create_orders && <Notice tone="bad">{m.orders_blocked_message}</Notice>}
      {!m.purchases_enabled && <Notice tone="warn">Покупка временно приостановлена.</Notice>}

      <div className="stats">
        <div className="stat">
          <div className="stat-label">Куплено всего</div>
          <div className="stat-value">
            <Money value={formatUsdt(m.total_bought)} />
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Выведено всего</div>
          <div className="stat-value">
            <Money value={formatUsdt(m.total_withdrawn)} />
          </div>
        </div>
      </div>

      {items.length > 0 && (
        <>
          <div className="section-title">
            <h3>Последние операции</h3>
            <button onClick={() => nav("/history")}>Все</button>
          </div>
          <div className="card list">
            {items.map((it) => (
              <button key={`${it.kind}-${it.id}`} className="tile" onClick={() => nav("/history")}>
                <HistoryBubble item={it} />
                <span className="tile-body">
                  <span className="tile-title">{it.kind === "order" ? "Покупка" : "Вывод"}</span>
                  <div className="tile-sub">{formatDateTime(it.created_at)}</div>
                </span>
                <HistoryEnd item={it} />
              </button>
            ))}
          </div>
        </>
      )}

      <p className="support">Вопросы? Напишите в поддержку: {m.support_contact}</p>
    </div>
  );
}

export function HistoryBubble({ item }: { item: HistoryItem }) {
  const done = item.status === "paid" || item.status === "completed";
  return <Bubble icon={item.kind === "order" ? "down" : "up"} tone={done ? (item.kind === "order" ? "ok" : "brand") : "muted"} />;
}

export function HistoryEnd({ item }: { item: HistoryItem }) {
  const credited = item.kind === "order" && item.status === "paid";
  return (
    <span className="tile-end">
      <div className={`tile-amount ${credited ? "plus" : ""}`}>
        {item.kind === "order" ? "+" : "−"}
        {formatUsdt(item.usdt)}
      </div>
      <div className={`tile-status tone-${statusTone(item.status)}`}>{item.status_label}</div>
    </span>
  );
}
