import { Link, Navigate, useNavigate } from "react-router-dom";
import { formatRate, formatUsdt } from "@shared/format";
import { ErrorView, Loader, Notice, useLoad } from "@shared/ui";
import { api, type Me, type Rate } from "../api";

export function Home() {
  const nav = useNavigate();
  const me = useLoad(() => api.get<Me>("/api/client/me"));
  const rate = useLoad(() => api.get<Rate>("/api/client/rate"));

  if (me.loading) return <Loader />;
  if (me.error || !me.data) return <ErrorView error={me.error} onRetry={() => me.reload()} />;
  const m = me.data;
  if (!m.registered && !m.blocked) return <Navigate to="/register" replace />;

  return (
    <div>
      {m.blocked && <Notice tone="bad">Ваш аккаунт заблокирован. Обратитесь в поддержку: {m.support_contact}</Notice>}
      <div className="card">
        <div className="muted small">Доступно</div>
        <div className="balance" data-testid="balance">{formatUsdt(m.balance)}</div>
        {m.hold !== "0.00" && <div className="muted small">В обработке: {formatUsdt(m.hold)}</div>}
        <div className="muted small" style={{ marginTop: 8 }}>
          {rate.data ? (
            <>
              Курс с учётом комиссии: <b>{formatRate(rate.data.effective_rate)}</b> за 1 USDT
            </>
          ) : rate.loading ? (
            "Загрузка курса…"
          ) : (
            "Курс временно недоступен"
          )}
        </div>
      </div>

      {m.active_order && (
        <Link to={`/order/${m.active_order.id}`} style={{ textDecoration: "none", color: "inherit" }}>
          <Notice tone="warn">
            Активная заявка <b>{m.active_order.public_id}</b> — {m.active_order.status_label.toLowerCase()}. Открыть →
          </Notice>
        </Link>
      )}
      {!m.can_create_orders && <Notice tone="bad">{m.orders_blocked_message}</Notice>}
      {!m.purchases_enabled && <Notice tone="warn">Покупка временно приостановлена.</Notice>}

      <div className="btn-row" style={{ margin: "12px 0" }}>
        <button className="btn btn-primary" onClick={() => nav("/buy")} disabled={m.blocked}>
          Купить
        </button>
        <button className="btn btn-secondary" onClick={() => nav("/withdraw")} disabled={m.blocked}>
          Вывести
        </button>
      </div>
      <button className="btn btn-ghost btn-block" onClick={() => nav("/history")}>
        История операций
      </button>
      <p className="muted small" style={{ textAlign: "center" }}>
        Поддержка: {m.support_contact}
      </p>
    </div>
  );
}
