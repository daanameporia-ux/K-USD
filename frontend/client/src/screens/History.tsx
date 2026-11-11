import { useState } from "react";
import { formatDateTime, formatRub, formatUsdt, shortAddress } from "@shared/format";
import { openLink } from "@shared/telegram";
import { AsyncButton, Empty, ErrorView, Loader, Row, StatusBadge, errorMessage, useLoad } from "@shared/ui";
import { api, type HistoryItem } from "../api";
import { TopBar } from "./common";

interface Page {
  items: HistoryItem[];
  next_cursor: string | null;
}

export function History() {
  const first = useLoad(() => api.get<Page>("/api/client/history?limit=20"));
  const [extra, setExtra] = useState<HistoryItem[]>([]);
  const [cursor, setCursor] = useState<string | null | undefined>(undefined);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (first.loading) return <Loader />;
  if (first.error || !first.data) return <ErrorView error={first.error} onRetry={() => first.reload()} />;
  const items = [...first.data.items, ...extra];
  const next = cursor === undefined ? first.data.next_cursor : cursor;

  return (
    <div>
      <TopBar title="История" />
      {items.length === 0 ? (
        <Empty text="Операций пока нет." />
      ) : (
        <div className="card">
          {items.map((it) => {
            const key = `${it.kind}-${it.id}`;
            return (
              <div key={key}>
                <div className="list-item" onClick={() => setOpen(open === key ? null : key)} role="button">
                  <div>
                    <div>
                      {it.kind === "order" ? "💵" : "📤"} <b>{it.public_id}</b>
                    </div>
                    <div className="muted small">{formatDateTime(it.created_at)}</div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <div className="num">
                      {it.kind === "order" ? `${formatRub(it.amount_rub)} → ` : "−"}
                      {formatUsdt(it.usdt)}
                    </div>
                    <StatusBadge status={it.status} label={it.status_label} />
                  </div>
                </div>
                {open === key && (
                  <div className="small" style={{ padding: "8px 0" }}>
                    {it.kind === "order" ? (
                      <Row label="Курс">{it.rate} ₽</Row>
                    ) : (
                      <>
                        <Row label="Сеть">{it.network}</Row>
                        <Row label="Адрес">
                          <span className="mono">{shortAddress(it.address, 8)}</span>
                        </Row>
                        <Row label="Комиссия">{formatUsdt(it.fee)}</Row>
                        <Row label="К получению">{formatUsdt(it.amount_to_send)}</Row>
                        {it.reject_reason && <Row label="Причина отказа">{it.reject_reason}</Row>}
                        {it.explorer_url && (
                          <button className="btn btn-ghost" onClick={() => openLink(it.explorer_url!)}>
                            Транзакция в эксплорере
                          </button>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      {error && <div className="field-error">{error}</div>}
      {next && (
        <AsyncButton
          variant="secondary"
          className="btn-block"
          onClick={async () => {
            try {
              const page = await api.get<Page>(`/api/client/history?limit=20&cursor=${encodeURIComponent(next)}`);
              setExtra((prev) => [...prev, ...page.items]);
              setCursor(page.next_cursor);
            } catch (e) {
              setError(errorMessage(e));
            }
          }}
        >
          Показать ещё
        </AsyncButton>
      )}
    </div>
  );
}
