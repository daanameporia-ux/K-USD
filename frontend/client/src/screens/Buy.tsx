import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, Attempt } from "@shared/api";
import { formatRate, formatRub, formatUsdt, sanitizeRubInput, validateRub } from "@shared/format";
import { haptic } from "@shared/telegram";
import { AsyncButton, ErrorView, Loader, Notice, Row, errorMessage, useLoad } from "@shared/ui";
import { api, type Me, type Order, type Quote } from "../api";
import { AmountField, Bubble, Footer, Money, TopBar } from "./common";

const QUICK = [1000, 5000, 10000, 50000];

export function Buy() {
  const nav = useNavigate();
  const me = useLoad(() => api.get<Me>("/api/client/me"));
  const [text, setText] = useState("");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoteError, setQuoteError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const attempt = useRef(new Attempt());

  const limits = me.data ? { min: Number(me.data.limits.min_rub), max: Number(me.data.limits.max_rub) } : null;
  const check = useMemo(() => (limits ? validateRub(text, limits.min, limits.max) : null), [text, limits?.min, limits?.max]);

  // Live preview: every input change starts a new sequence number; a response of an older request (or one for
  // another amount) is dropped, so a slow quote for a previous amount can never be shown or confirmed.
  const previewSeq = useRef(0);
  useEffect(() => {
    const my = ++previewSeq.current;
    setQuote(null);
    setQuoteError(null);
    if (!check?.value) return;
    const amount = check.value;
    const t = setTimeout(async () => {
      try {
        const q = await api.post<Quote>("/api/client/orders/preview", { amount_rub: String(amount) });
        if (my !== previewSeq.current || Number(q.amount_rub) !== amount) return;
        setQuote(q);
      } catch (e) {
        if (my === previewSeq.current) setQuoteError(errorMessage(e));
      }
    }, 300);
    return () => {
      clearTimeout(t);
      previewSeq.current += 1; // the input changed or the screen closed: drop the in-flight answer
    };
  }, [check?.value]);
  // «Продолжить» only for a quote of exactly the amount in the input.
  const quoteReady = !!quote && !!check?.value && Number(quote.amount_rub) === check.value;

  if (me.loading) return <Loader />;
  if (me.error || !me.data) return <ErrorView error={me.error} onRetry={() => me.reload()} />;
  if (me.data.active_order) {
    return (
      <div className="screen">
        <TopBar title="Покупка" />
        <Notice tone="warn">У вас уже есть активная заявка {me.data.active_order.public_id}.</Notice>
        <Footer>
          <button className="btn btn-primary btn-block" onClick={() => nav(`/order/${me.data!.active_order!.id}`)}>
            Открыть заявку
          </button>
        </Footer>
      </div>
    );
  }

  async function create() {
    if (!quote) return;
    setNotice(null);
    const body = { amount_rub: quote.amount_rub, expected_usdt: quote.usdt_amount };
    try {
      // the key is reused only when resending exactly this request; a new amount / quote gets a new key
      const order = await api.post<Order>("/api/client/orders", body, attempt.current.keyFor(body));
      attempt.current.done();
      if (order.status === "failed") {
        setNotice(order.error?.message ?? "Не удалось создать платёж, попробуйте ещё раз.");
        haptic("error");
        return;
      }
      haptic("success");
      nav(`/order/${order.id}`, { replace: true });
    } catch (e) {
      if (e instanceof ApiError && e.code === "price_changed") {
        attempt.current.done();
        try {
          setQuote(await api.post<Quote>("/api/client/orders/preview", { amount_rub: quote.amount_rub }));
          setNotice("Курс изменился, сумма к зачислению пересчитана. Проверьте и подтвердите ещё раз.");
        } catch (e2) {
          setNotice(`Курс изменился, но пересчитать сумму не удалось: ${errorMessage(e2)}`);
          haptic("error");
        }
        return;
      }
      if (e instanceof ApiError && e.code === "active_order_exists") {
        const id = (e.details as { order_id?: number }).order_id;
        if (id) nav(`/order/${id}`, { replace: true });
        return;
      }
      setNotice(errorMessage(e));
      haptic("error");
    }
  }

  if (confirming && quote) {
    return (
      <div className="screen">
        <TopBar title="Подтверждение" back={null} />
        <div className="order-head">
          <div className="amount-label">Вы платите</div>
          <div className="order-amount num">
            <Money value={formatRub(quote.amount_rub)} />
          </div>
        </div>
        <div className="card">
          <Row label="Курс">{formatRate(quote.rate)}</Row>
          <Row label="Комиссия сервиса">
            {quote.markup_pct}% ({formatRub(quote.fee_rub)})
          </Row>
          <div className="row row-total">
            <span className="row-label">К зачислению</span>
            <span className="row-value">{formatUsdt(quote.usdt_amount)}</span>
          </div>
        </div>
        {notice && <Notice tone="warn">{notice}</Notice>}
        <Footer>
          <AsyncButton onClick={create}>Подтвердить</AsyncButton>
          <button
            className="btn btn-ghost"
            onClick={() => {
              setNotice(null);
              setConfirming(false);
            }}
          >
            Изменить
          </button>
        </Footer>
      </div>
    );
  }

  return (
    <div className="screen">
      <TopBar title="Купить USDT" />
      <div className="amount">
        <div className="amount-label">Сумма покупки</div>
        <AmountField
          unit="₽"
          inputMode="numeric"
          pattern="[0-9 ]*"
          placeholder="0"
          value={text}
          aria-label="Сумма в рублях"
          onChange={(e) => setText(sanitizeRubInput(e.target.value))}
        />
        <div className={`amount-hint ${check?.error ? "error" : ""}`}>
          {check?.error ?? `от ${formatRub(me.data.limits.min_rub)} до ${formatRub(me.data.limits.max_rub)}`}
        </div>
      </div>
      <div className="quick">
        {QUICK.map((q) => (
          <button
            key={q}
            className={`chip ${check?.value === q ? "chip-active" : ""}`}
            onClick={() => setText(sanitizeRubInput(String(q)))}
          >
            {formatRub(q)}
          </button>
        ))}
      </div>
      <div className="card">
        <div className="receive">
          <Bubble icon="down" tone="ok" />
          <div>
            <div className="receive-label">Вы получите</div>
            <div className="big-usdt num" data-testid="quote">
              {quoteReady ? <Money value={formatUsdt(quote!.usdt_amount)} /> : "—"}
            </div>
          </div>
        </div>
        {quoteReady && quote && (
          <div className="receive-meta">
            <Row label="Курс">{formatRate(quote.rate)}</Row>
            <Row label="Комиссия">
              {quote.markup_pct}% ({formatRub(quote.fee_rub)})
            </Row>
          </div>
        )}
        {quoteError && <div className="field-error">{quoteError}</div>}
      </div>
      <Footer>
        <button className="btn btn-primary btn-block" disabled={!quoteReady} onClick={() => quoteReady && setConfirming(true)}>
          Продолжить
        </button>
      </Footer>
    </div>
  );
}
