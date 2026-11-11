import { useEffect, useRef, useState } from "react";
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
  const [qr, setQr] = useState<{ id: string | undefined; url: string } | null>(null);
  const [qrError, setQrError] = useState(false);
  const [qrAttempt, setQrAttempt] = useState(0);
  // The blob URL currently shown; revoked only when it is replaced or the screen unmounts.
  const qrRef = useRef<{ id: string | undefined; url: string } | null>(null);
  // While the user's action (cancel) is in flight the poll is paused, so it cannot race the action's result.
  const acting = useRef(false);
  const [now, setNow] = useState(Date.now());
  const [error, setError] = useState<string | null>(null);
  const status = order.data?.status;
  const active = status === "pending" || status === "creating";
  const pending = status === "pending";

  useEffect(() => {
    if (!active) return;
    const poll = setInterval(() => {
      if (!acting.current) void order.reload(true);
    }, 3000);
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(poll);
      clearInterval(tick);
    };
  }, [active, order.reload]);

  useEffect(() => {
    if (status === "paid") haptic("success");
  }, [status]);

  const replaceQr = (next: { id: string | undefined; url: string } | null) => {
    if (qrRef.current && qrRef.current.url !== next?.url) URL.revokeObjectURL(qrRef.current.url);
    qrRef.current = next;
    setQr(next);
  };

  useEffect(() => {
    if (!pending) return;
    if (qrRef.current?.id === id) return; // already loaded for this order
    if (qrRef.current) replaceQr(null); // QR of another order
    let cancelled = false;
    setQrError(false);
    api
      .blobUrl(`/api/client/orders/${id}/qr.png`)
      .then((url) => {
        if (cancelled) URL.revokeObjectURL(url);
        else replaceQr({ id, url });
      })
      .catch(() => {
        if (!cancelled) setQrError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [pending, id, qrAttempt]);

  // Revoke the last blob URL only when the screen goes away.
  useEffect(
    () => () => {
      if (qrRef.current) URL.revokeObjectURL(qrRef.current.url);
      qrRef.current = null;
    },
    [],
  );

  const retryQr = () => {
    replaceQr(null);
    setQrError(false);
    setQrAttempt((n) => n + 1);
  };

  if (!order.data) {
    if (order.loading || !order.error) return <Loader />;
    return <ErrorView error={order.error} onRetry={() => order.reload()} />;
  }
  const o = order.data;
  const left = new Date(o.expires_at).getTime() - now;
  const final = FINAL[o.status];
  const qrUrl = qr && qr.id === id ? qr.url : null;

  return (
    <div>
      <TopBar title={`Заявка ${o.public_id}`} />
      {order.error ? (
        <Notice tone="warn">
          <span data-testid="poll-error">
            Не удалось обновить статус: {errorMessage(order.error)}
            {active ? " Повторим автоматически." : ""}
          </span>
        </Notice>
      ) : null}
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
          {qrError ? (
            <div className="state state-error" role="alert" data-testid="qr-error">
              <p>Не удалось загрузить QR-код. Можно оплатить кнопкой «Оплатить» или попробовать ещё раз.</p>
              <button className="btn btn-secondary" onClick={retryQr}>
                Повторить
              </button>
            </div>
          ) : qrUrl ? (
            <img
              className="qr"
              src={qrUrl}
              alt="QR-код для оплаты по СБП"
              onError={() => {
                replaceQr(null);
                setQrError(true);
              }}
            />
          ) : (
            <Loader text="Готовим QR-код…" />
          )}
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
              setError(null);
              acting.current = true;
              try {
                order.setData(await api.post<Order>(`/api/client/orders/${o.id}/cancel`));
              } catch (e) {
                setError(errorMessage(e));
                void order.reload(true);
              } finally {
                acting.current = false;
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
