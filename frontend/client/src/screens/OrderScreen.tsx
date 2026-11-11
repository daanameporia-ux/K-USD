import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { formatCountdown, formatRub, formatUsdt } from "@shared/format";
import { confirmAction, haptic, openLink } from "@shared/telegram";
import { AsyncButton, ErrorView, Loader, Notice, Row, StatusBadge, errorMessage, useLoad } from "@shared/ui";
import { api, type Order } from "../api";
import { TopBar } from "./common";

const FINAL: Record<string, { tone: "ok" | "warn" | "bad" | "info"; text: (o: Order) => string }> = {
  paid: { tone: "ok", text: (o) => `Оплата получена, зачислено ${formatUsdt(o.credited_usdt ?? o.usdt_amount)}.` },
  expired: { tone: "warn", text: () => "Заявка истекла. Если нужно, создайте новую." },
  cancelled: { tone: "info", text: () => "Заявка отменена." },
  failed: { tone: "bad", text: () => "Не удалось создать платёж, попробуйте ещё раз." },
  review: { tone: "warn", text: () => "Платёж получен и проходит проверку. Мы сообщим результат." },
  rejected: { tone: "bad", text: () => "Заявка отклонена после проверки." },
};

export function OrderScreen() {
  const { id } = useParams();
  const nav = useNavigate();
  const order = useLoad(() => api.get<Order>(`/api/client/orders/${id}`), [id]);
  const [qr, setQr] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const [error, setError] = useState<string | null>(null);
  const status = order.data?.status;
  const active = status === "pending" || status === "creating";

  useEffect(() => {
    if (!active) return;
    const poll = setInterval(() => void order.reload(true), 3000);
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(poll);
      clearInterval(tick);
    };
  }, [active, order.reload]);

  useEffect(() => {
    if (status === "paid") haptic("success");
  }, [status]);

  useEffect(() => {
    if (status !== "pending" || qr) return;
    let url: string | null = null;
    api
      .blobUrl(`/api/client/orders/${id}/qr.png`)
      .then((u) => {
        url = u;
        setQr(u);
      })
      .catch(() => setQr(null));
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [status, id, qr]);

  if (order.loading) return <Loader />;
  if (order.error || !order.data) return <ErrorView error={order.error} onRetry={() => order.reload()} />;
  const o = order.data;
  const left = new Date(o.expires_at).getTime() - now;
  const final = FINAL[o.status];

  return (
    <div>
      <TopBar title={`Заявка ${o.public_id}`} />
      <div className="card stack">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span className="big-usdt">{formatRub(o.amount_rub)}</span>
          <StatusBadge status={o.status} label={o.status_label} />
        </div>
        <Row label="Будет зачислено">{formatUsdt(o.usdt_amount)}</Row>
        <Row label="Курс">{o.rate} ₽</Row>
      </div>

      {o.status === "pending" && (
        <div className="card stack" data-testid="payment">
          {qr ? <img className="qr" src={qr} alt="QR-код для оплаты по СБП" /> : <Loader text="Готовим QR-код…" />}
          <div>
            <div className="muted small" style={{ textAlign: "center" }}>
              Оплатить в течение
            </div>
            <div className="timer">{formatCountdown(left)}</div>
          </div>
          <button className="btn btn-primary btn-block" onClick={() => o.qr_url && openLink(o.qr_url)}>
            Оплатить
          </button>
          <p className="muted small">
            Отсканируйте QR-код в приложении банка или нажмите «Оплатить». Оплатите ровно {formatRub(o.amount_rub)}
            одним платежом. Статус обновится автоматически.
          </p>
          {error && <Notice tone="bad">{error}</Notice>}
          <AsyncButton
            variant="danger"
            onClick={async () => {
              if (!(await confirmAction(`Отменить заявку ${o.public_id}?`))) return;
              try {
                order.setData(await api.post<Order>(`/api/client/orders/${o.id}/cancel`));
              } catch (e) {
                setError(errorMessage(e));
                void order.reload(true);
              }
            }}
          >
            Отменить заявку
          </AsyncButton>
        </div>
      )}
      {o.status === "creating" && <Loader text="Создаём платёж…" />}
      {final && <Notice tone={final.tone}>{final.text(o)}</Notice>}
      {!active && (
        <button className="btn btn-secondary btn-block" style={{ marginTop: 12 }} onClick={() => nav("/")}>
          На главную
        </button>
      )}
    </div>
  );
}
