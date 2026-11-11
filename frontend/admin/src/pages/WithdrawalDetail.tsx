import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { formatDateTime, formatUsdt } from "@shared/format";
import { confirmAction } from "@shared/telegram";
import { AsyncButton, CopyButton, ErrorView, Loader, Notice, Row, StatusBadge, useLoad } from "@shared/ui";
import { adminPath, api } from "../api";
import { useMe } from "../auth";
import { ExternalLink, PageHeader, Section, useAction } from "../components";
import { isAdminOnlyAmount, staffTitle, userTitle, verifyRows } from "../helpers";
import type { Withdrawal } from "../types";

const FINAL = ["completed", "rejected", "cancelled"];

export function WithdrawalDetailPage() {
  const { id } = useParams();
  const me = useMe();
  const { data: w, error, loading, reload, setData } = useLoad(
    () => api.get<Withdrawal>(adminPath(`/withdrawals/${id}`)),
    [id],
  );
  const action = useAction();
  const [hash, setHash] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [forceOpen, setForceOpen] = useState(false);
  const [forceComment, setForceComment] = useState("");

  // While the blockchain check is running, refresh every 5 seconds.
  const verifying = w?.status === "verifying";
  useEffect(() => {
    if (!verifying) return;
    const t = window.setInterval(() => void reload(true), 5000);
    return () => window.clearInterval(t);
  }, [verifying, reload]);

  if (loading && !w) return <Loader />;
  if (error && !w) return <ErrorView error={error} onRetry={() => void reload()} />;
  if (!w) return null;

  const path = (a: string) => adminPath(`/withdrawals/${w.id}/${a}`);
  const adminOnly = isAdminOnlyAmount(w.amount, me.withdraw_admin_only_from_usdt, me.b6_enabled);
  const isFinal = FINAL.includes(w.status);
  const mine = w.assigned_staff_id === me.staff.id;
  const canWork = mine || me.is_admin;
  const rows = verifyRows(w.verify_details);
  const explorer = w.last_explorer_url ?? w.explorer_url;

  async function act(fn: () => Promise<Withdrawal>, success: string, after?: () => void) {
    const ok = await action.run(async () => setData(await fn()), success);
    if (ok) after?.();
    else void reload(true);
  }

  async function take() {
    if (!(await confirmAction(`Взять в работу ${w!.public_id} на ${formatUsdt(w!.amount_to_send)}?`))) return;
    await act(() => api.post<Withdrawal>(path("take")), "Вывод взят в работу.");
  }

  async function release() {
    if (!(await confirmAction(`Вернуть ${w!.public_id} в очередь?`))) return;
    await act(() => api.post<Withdrawal>(path("release")), "Вывод возвращён в очередь.");
  }

  async function submitHash() {
    const tx = hash.trim();
    if (!tx) {
      action.setError("Введите хэш транзакции.");
      return;
    }
    if (!(await confirmAction(`Отправить хэш на проверку?\n${tx}`))) return;
    await act(() => api.post<Withdrawal>(path("submit-hash"), { tx_hash: tx }), "Хэш принят, идёт проверка.", () =>
      setHash(""),
    );
  }

  async function reject() {
    const r = reason.trim();
    if (r.length < 3) {
      action.setError("Укажите причину отклонения (её увидит клиент).");
      return;
    }
    if (!(await confirmAction(`Отклонить ${w!.public_id}? Средства вернутся на баланс клиента.`))) return;
    await act(() => api.post<Withdrawal>(path("reject"), { reason: r }), "Вывод отклонён.", () => {
      setRejectOpen(false);
      setReason("");
    });
  }

  async function recheck() {
    await act(() => api.post<Withdrawal>(path("recheck")), "Проверка выполнена.");
  }

  async function forceComplete() {
    const c = forceComment.trim();
    if (!c) {
      action.setError("Комментарий обязателен.");
      return;
    }
    if (!(await confirmAction(`Принудительно закрыть ${w!.public_id} без проверки в блокчейне? Действие необратимо.`)))
      return;
    await act(() => api.post<Withdrawal>(path("force-complete"), { comment: c }), "Вывод закрыт принудительно.", () => {
      setForceOpen(false);
      setForceComment("");
    });
  }

  return (
    <div className="page">
      <PageHeader title={w.public_id} back="/withdrawals">
        <StatusBadge status={w.status} label={w.status_label} />
        {w.forced && <span className="badge badge-warn">закрыт принудительно</span>}
      </PageHeader>

      {adminOnly && !isFinal && (
        <Notice tone="warn">
          Сумма от {formatUsdt(me.withdraw_admin_only_from_usdt)} — закрыть этот вывод может только админ
          {me.is_admin ? "." : ". Вы можете взять его в работу и отправить, но хэш подтвердит админ."}
        </Notice>
      )}

      <div className="detail-grid">
        <Section title="Перевод">
          <Row label="Клиент">
            {w.user ? (
              <>
                <Link to={`/users/${w.user.id}`}>{userTitle(w.user)}</Link>
                {w.user.is_suspicious && <span className="badge badge-bad ml">подозрительный</span>}
              </>
            ) : (
              "—"
            )}
          </Row>
          <Row label="Сеть">{w.network_label}</Row>
          <div className="copy-block">
            <div className="muted small">Адрес получателя</div>
            <div className="mono copy-value">{w.address}</div>
            <CopyButton value={w.address} label="Копировать адрес" />
          </div>
          <div className="copy-block">
            <div className="muted small">Сумма к отправке</div>
            <div className="copy-value big num">{formatUsdt(w.amount_to_send)}</div>
            <CopyButton value={w.amount_to_send} label="Копировать сумму" />
          </div>
          <Row label="Списано с баланса">{formatUsdt(w.amount)}</Row>
          <Row label="Комиссия">{formatUsdt(w.fee)}</Row>
          <Row label="Создан">{formatDateTime(w.created_at)}</Row>
          <Row label="Взят в работу">
            {w.assigned_staff ? `${staffTitle(w.assigned_staff)} · ${formatDateTime(w.taken_at)}` : "—"}
          </Row>
          {w.finished_at && <Row label="Завершён">{formatDateTime(w.finished_at)}</Row>}
          {w.reject_reason && <Row label="Причина отклонения">{w.reject_reason}</Row>}
        </Section>

        <Section title="Проверка транзакции">
          {w.verify_label ? (
            <Notice tone={w.verify_status === "ok" ? "ok" : verifying ? "info" : "warn"}>
              <b>{w.verify_label}</b>
              {verifying && <span className="muted"> · обновляется автоматически</span>}
            </Notice>
          ) : (
            <p className="muted">Хэш ещё не вносился.</p>
          )}
          {w.last_tx_hash && (
            <div className="copy-block">
              <div className="muted small">Хэш</div>
              <div className="mono copy-value">{w.last_tx_hash}</div>
              <CopyButton value={w.last_tx_hash} label="Копировать хэш" />
            </div>
          )}
          {explorer && (
            <p>
              <ExternalLink href={explorer}>Открыть в эксплорере ↗</ExternalLink>
            </p>
          )}
          {rows.length > 0 && (
            <div className="table-wrap">
              <table className="table table-compact">
                <thead>
                  <tr>
                    <th>Поле</th>
                    <th>Ожидалось</th>
                    <th>Фактически</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.key} className={r.mismatch ? "tr-bad" : undefined}>
                      <td>{r.label}</td>
                      <td className="mono">{r.expected}</td>
                      <td className="mono">
                        {r.actual}
                        {r.mismatch && <span className="badge badge-bad ml">не совпадает</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {w.verify_details?.error && <p className="muted small">Ошибка: {String(w.verify_details.error)}</p>}
          {w.verify_attempts > 0 && (
            <p className="muted small">
              Попыток проверки: {w.verify_attempts} · последняя {formatDateTime(w.last_verify_at)}
            </p>
          )}
        </Section>
      </div>

      {!isFinal && (
        <Section title="Действия">
          <div className="stack">
            {action.view}
            {w.status === "in_progress" && !canWork && (
              <Notice tone="info">Вывод в работе у {staffTitle(w.assigned_staff ?? null)}.</Notice>
            )}

            {w.status === "pending" && (
              <div className="btn-row">
                <AsyncButton onClick={take}>Взять в работу</AsyncButton>
              </div>
            )}

            {w.status === "in_progress" && canWork && (
              <div className="stack">
                <label className="field">
                  <span>Хэш отправленной транзакции</span>
                  <input
                    className="input mono"
                    value={hash}
                    onChange={(e) => setHash(e.target.value)}
                    placeholder={w.network === "TRC20" ? "64 hex-символа" : "0x + 64 hex-символа"}
                    maxLength={200}
                    autoComplete="off"
                    spellCheck={false}
                  />
                </label>
                <div className="btn-row">
                  <AsyncButton onClick={submitHash} disabled={!hash.trim()}>
                    Отправить на проверку
                  </AsyncButton>
                  <AsyncButton variant="secondary" onClick={release}>
                    Вернуть в очередь
                  </AsyncButton>
                </div>
              </div>
            )}

            {verifying && (
              <div className="btn-row">
                <AsyncButton variant="secondary" onClick={recheck}>
                  Перепроверить сейчас
                </AsyncButton>
              </div>
            )}

            {(w.status === "pending" || (w.status === "in_progress" && canWork)) && (
              <div className="stack">
                {!rejectOpen ? (
                  <div className="btn-row">
                    <button className="btn btn-danger" onClick={() => setRejectOpen(true)}>
                      Отклонить…
                    </button>
                  </div>
                ) : (
                  <div className="subform stack">
                    <label className="field">
                      <span>Причина отклонения (увидит клиент)</span>
                      <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} />
                    </label>
                    <div className="btn-row">
                      <AsyncButton variant="danger" onClick={reject} disabled={reason.trim().length < 3}>
                        Отклонить вывод
                      </AsyncButton>
                      <button className="btn btn-ghost" onClick={() => setRejectOpen(false)}>
                        Отмена
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {me.is_admin && (w.status === "in_progress" || w.status === "verifying") && (
              <div className="stack">
                {!forceOpen ? (
                  <div className="btn-row">
                    <button className="btn btn-ghost" onClick={() => setForceOpen(true)}>
                      Закрыть принудительно…
                    </button>
                  </div>
                ) : (
                  <div className="subform stack">
                    <Notice tone="warn">
                      Вывод будет помечен исполненным без проверки в блокчейне. Запись попадёт в аудит, в служебный чат
                      уйдёт алерт.
                    </Notice>
                    <label className="field">
                      <span>Комментарий (обязательно)</span>
                      <textarea
                        rows={3}
                        value={forceComment}
                        onChange={(e) => setForceComment(e.target.value)}
                        maxLength={1000}
                      />
                    </label>
                    <div className="btn-row">
                      <AsyncButton variant="danger" onClick={forceComplete} disabled={!forceComment.trim()}>
                        Закрыть без проверки
                      </AsyncButton>
                      <button className="btn btn-ghost" onClick={() => setForceOpen(false)}>
                        Отмена
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </Section>
      )}
      {isFinal && action.view}
    </div>
  );
}
