import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { formatCountdown, formatRub, formatUsdt } from "@shared/format";
import { confirmAction, haptic, openLink } from "@shared/telegram";
import { AsyncButton, ErrorView, Loader, Notice, Row, StatusBadge, errorMessage, useLoad } from "@shared/ui";
import { api, type Order } from "../api";
import { Footer, Icon, Money, Result, TopBar, type IconName } from "./common";

type Final = { tone: "ok" | "warn" | "bad" | "info"; icon: IconName; title: string; text: (o: Order) => string };
const FINAL: Record<string, Final> = {
  paid: {
    tone: "ok",
    icon: "check",
    title: "Оплата получена",
    text: (o) => `На баланс зачислено ${formatUsdt(o.credited_usdt ?? o.usdt_amount)}.`,
  },
  expired: { tone: "warn", icon: "clock", title: "Время вышло", text: () => "Заявка истекла. Если нужно, создайте новую." },
  cancelled: { tone: "info", icon: "cross", title: "Отменено", text: () => "Заявка отменена." },
  failed: { tone: "bad", icon: "alert", title: "Ошибка", text: () => "Не удалось создать платёж, попробуйте ещё раз." },
  review: {
    tone: "warn",
    icon: "shield",
    title: "Проверяем платёж",
    text: () => "Платёж получен и проходит проверку. Мы сообщим результат.",
  },
  rejected: { tone: "bad", icon: "cross", title: "Отклонено", text: () => "Заявка отклонена после проверки." },
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

  const cancel = (
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
  );

  return (
    <div className="screen">
      <TopBar title={`Заявка ${o.public_id}`} />
      {order.error ? (
        <Notice tone="warn">
          <span data-testid="poll-error">
            Не удалось обновить статус: {errorMessage(order.error)}
            {active ? " Повторим автоматически." : ""}
          </span>
        </Notice>
      ) : null}

      {final ? (
        <Result icon={final.icon} tone={final.tone} title={final.title}>
          {final.text(o)}
        </Result>
      ) : (
        <div className="order-head">
          <div className="order-amount num">
            <Money value={formatRub(o.amount_rub)} />
          </div>
          <StatusBadge status={o.status} label={o.status_label} />
        </div>
      )}

      {o.status === "pending" && (
        <div className="card qr-card" data-testid="payment">
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
            <span className="timer-pill">
              <Icon name="clock" size={16} />
              Оплатить в течение <span className="timer num">{formatCountdown(left)}</span>
            </span>
          </div>
          <p className="help">
            Отсканируйте QR-код в приложении банка или нажмите «Оплатить». Оплатите ровно {formatRub(o.amount_rub)}{" "}
            одним платежом. Статус обновится автоматически.
          </p>
        </div>
      )}
      {o.status === "creating" && <Loader text="Создаём платёж…" />}

      <div className="card">
        {final && <Row label="Сумма">{formatRub(o.amount_rub)}</Row>}
        <Row label={o.status === "paid" ? "Зачислено" : "Будет зачислено"}>
          {formatUsdt(o.status === "paid" ? (o.credited_usdt ?? o.usdt_amount) : o.usdt_amount)}
        </Row>
        <Row label="Курс">{o.rate} ₽</Row>
      </div>

      {error && <Notice tone="bad">{error}</Notice>}
      <Footer>
        {o.status === "pending" && (
          <>
            <button className="btn btn-primary btn-block" onClick={() => o.qr_url && openLink(o.qr_url)}>
              Оплатить
            </button>
            {cancel}
          </>
        )}
        {!active && (
          <button className="btn btn-primary btn-block" onClick={() => nav("/")}>
            На главную
          </button>
        )}
      </Footer>
    </div>
  );
}
