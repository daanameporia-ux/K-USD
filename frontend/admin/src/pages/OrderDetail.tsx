import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { formatDateTime, formatRate, formatRub, formatUsdt } from "@shared/format";
import { confirmAction } from "@shared/telegram";
import { AsyncButton, CopyButton, ErrorView, Loader, Notice, Row, StatusBadge, useLoad } from "@shared/ui";
import { adminPath, api } from "../api";
import { useMe } from "../auth";
import { ExternalLink, PageHeader, Section, useAction } from "../components";
import { compactJson, formatPct, parsePositiveInt, userTitle } from "../helpers";
import type { Order } from "../types";

interface SimulateResult {
  callback_status: string;
  result: unknown;
  order: Order;
}

export function OrderDetailPage() {
  const { id } = useParams();
  const me = useMe();
  const { data: o, error, loading, reload, setData } = useLoad(() => api.get<Order>(adminPath(`/orders/${id}`)), [id]);
  const action = useAction();
  const [comment, setComment] = useState("");
  const [otherOpen, setOtherOpen] = useState(false);
  const [otherAmount, setOtherAmount] = useState("");
  const [simResult, setSimResult] = useState<string | null>(null);

  if (loading && !o) return <Loader />;
  if (error && !o) return <ErrorView error={error} onRetry={() => void reload()} />;
  if (!o) return null;

  const user = o.user;
  const keep = (next: Order) => setData({ ...next, user: next.user ?? user });

  async function resolve(decision: "credit_order_rate" | "credit_current_rate" | "reject") {
    const c = comment.trim();
    if (decision === "reject" && !c) {
      action.setError("Для отклонения укажите комментарий.");
      return;
    }
    const text =
      decision === "credit_order_rate"
        ? `Зачислить по курсу заявки (${formatRate(o!.rate)})?`
        : decision === "credit_current_rate"
          ? "Зачислить по текущему курсу?"
          : "Отклонить заявку?";
    if (!(await confirmAction(`${o!.public_id}: ${text}`))) return;
    const ok = await action.run(async () => {
      keep(
        await api.post<Order>(adminPath(`/orders/${o!.id}/resolve-review`), {
          decision,
          comment: c || null,
        }),
      );
    }, "Решение сохранено.");
    if (ok) setComment("");
    else void reload(true);
  }

  async function simulate(amount: number | null) {
    const text = amount === null ? `Симулировать оплату ${formatRub(o!.amount_rub)}?` : `Симулировать оплату ${formatRub(amount)} (сумма отличается)?`;
    if (!(await confirmAction(text))) return;
    setSimResult(null);
    const ok = await action.run(async () => {
      const res = await api.post<SimulateResult>(
        adminPath(`/orders/${o!.id}/simulate-payment`),
        amount === null ? {} : { amount_rub: amount },
      );
      keep(res.order);
      setSimResult(`Callback: ${res.callback_status} · ${compactJson(res.result)}`);
    }, "Оплата симулирована.");
    if (ok) {
      setOtherOpen(false);
      setOtherAmount("");
    }
  }

  const other = parsePositiveInt(otherAmount);
  // Mock only: the backend accepts a simulated payment for any order that has a provider payment — also an expired /
  // cancelled one (a payment after the close goes to review).
  const lateOrder = o.status === "expired" || o.status === "cancelled";
  const canSimulate = me.is_mock && me.is_admin && (o.status === "pending" || (lateOrder && !!o.provider_payment_id));

  return (
    <div className="page">
      <PageHeader title={o.public_id} back="/orders">
        <StatusBadge status={o.status} label={o.status_label} />
      </PageHeader>

      <div className="detail-grid">
        <Section title="Заявка">
          <Row label="Клиент">
            <Link to={`/users/${o.user_id}`}>{userTitle(user ?? { id: o.user_id })}</Link>
          </Row>
          <Row label="Сумма">{formatRub(o.amount_rub)}</Row>
          {o.paid_amount_rub && <Row label="Оплачено">{formatRub(o.paid_amount_rub)}</Row>}
          <Row label="Курс">
            {formatRate(o.rate)} {o.rate_source ? <span className="muted small">({o.rate_source})</span> : null}
          </Row>
          <Row label="Наценка">{formatPct(o.markup_pct)}</Row>
          <Row label="USDT по заявке">{formatUsdt(o.usdt_amount)}</Row>
          <Row label="Зачислено">{formatUsdt(o.credited_usdt)}</Row>
          <Row label="Маржа">{formatRub(o.margin_rub)}</Row>
          <Row label="Источник">{o.source === "bot" ? "бот" : "Mini App"}</Row>
        </Section>
        <Section title="Платёж">
          <Row label="Провайдер">{o.provider ?? "—"}</Row>
          <Row label="ID платежа">
            {o.provider_payment_id ? (
              <>
                <span className="mono">{o.provider_payment_id}</span> <CopyButton value={o.provider_payment_id} />
              </>
            ) : (
              "—"
            )}
          </Row>
          {o.qr_url && (
            <Row label="QR">
              <ExternalLink href={o.qr_url}>ссылка ↗</ExternalLink>
            </Row>
          )}
          <Row label="Создана">{formatDateTime(o.created_at)}</Row>
          <Row label="Истекает">{formatDateTime(o.expires_at)}</Row>
          <Row label="Оплачена">{formatDateTime(o.paid_at)}</Row>
          <Row label="Завершена">{formatDateTime(o.finished_at)}</Row>
          {o.payer_info != null && <Row label="Плательщик">{compactJson(o.payer_info)}</Row>}
        </Section>
      </div>

      {o.status === "review" && (
        <Section title="Решение по разбору">
          <div className="stack">
            {o.review_reason && <Notice tone="warn">Причина разбора: {o.review_reason}</Notice>}
            {me.is_admin ? (
              <>
                <label className="field">
                  <span>Комментарий (обязателен для отклонения)</span>
                  <textarea rows={3} value={comment} maxLength={1000} onChange={(e) => setComment(e.target.value)} />
                </label>
                {action.view}
                <div className="btn-row">
                  <AsyncButton onClick={() => resolve("credit_order_rate")}>Зачислить по курсу заявки</AsyncButton>
                  <AsyncButton variant="secondary" onClick={() => resolve("credit_current_rate")}>
                    Зачислить по текущему курсу
                  </AsyncButton>
                  <AsyncButton variant="danger" onClick={() => resolve("reject")} disabled={!comment.trim()}>
                    Отклонить
                  </AsyncButton>
                </div>
              </>
            ) : (
              <p className="muted">Решение по заявкам на разборе принимает админ.</p>
            )}
          </div>
        </Section>
      )}

      {canSimulate && (
        <Section title="Тестовый платёж (mock)">
          <div className="stack">
            {lateOrder && (
              <Notice tone="info">
                Заявка уже закрыта. Оплата после закрытия не зачисляется автоматически — заявка уйдёт на разбор.
              </Notice>
            )}
            {action.view}
            <div className="btn-row">
              <AsyncButton onClick={() => simulate(null)}>Симулировать оплату</AsyncButton>
              <button className="btn btn-secondary" onClick={() => setOtherOpen((v) => !v)}>
                Симулировать оплату с другой суммой
              </button>
            </div>
            {otherOpen && (
              <div className="subform stack">
                <label className="field">
                  <span>Сумма оплаты, ₽ (целое число)</span>
                  <input
                    className="input"
                    inputMode="numeric"
                    value={otherAmount}
                    onChange={(e) => setOtherAmount(e.target.value.replace(/\D/g, "").slice(0, 9))}
                  />
                </label>
                {other === "invalid" && <div className="field-error">Введите целое положительное число.</div>}
                <AsyncButton
                  onClick={() => simulate(typeof other === "number" ? other : null)}
                  disabled={typeof other !== "number"}
                >
                  Отправить оплату
                </AsyncButton>
              </div>
            )}
          </div>
        </Section>
      )}
      {!(o.status === "review" || canSimulate) && action.view}
      {simResult && <p className="muted small mono">{simResult}</p>}
    </div>
  );
}
