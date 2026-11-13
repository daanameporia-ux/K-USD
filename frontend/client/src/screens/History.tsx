import { useState } from "react";
import { formatDateTime, formatRub, formatUsdt, shortAddress } from "@shared/format";
import { openLink } from "@shared/telegram";
import { AsyncButton, Empty, ErrorView, Loader, Row, errorMessage, useLoad } from "@shared/ui";
import { api, type HistoryItem } from "../api";
import { Icon, TopBar } from "./common";
import { HistoryBubble, HistoryEnd } from "./Home";

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
    <div className="screen">
      <TopBar title="История" />
      {items.length === 0 ? (
        <Empty text="Операций пока нет." />
      ) : (
        <div className="card list">
          {items.map((it) => {
            const key = `${it.kind}-${it.id}`;
            return (
              <div key={key} className="tile-wrap">
                <button className="tile" onClick={() => setOpen(open === key ? null : key)} aria-expanded={open === key}>
                  <HistoryBubble item={it} />
                  <span className="tile-body">
                    <span className="tile-title">{it.kind === "order" ? "Покупка" : "Вывод"}</span>
                    <div className="tile-sub">{formatDateTime(it.created_at)}</div>
                  </span>
                  <HistoryEnd item={it} />
                </button>
                {open === key && (
                  <div className="tile-details">
                    <Row label="Номер">{it.public_id}</Row>
                    {it.kind === "order" ? (
                      <>
                        <Row label="Сумма">{formatRub(it.amount_rub)}</Row>
                        <Row label="Курс">{it.rate} ₽</Row>
                      </>
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
                          <button className="btn btn-ghost btn-sm" onClick={() => openLink(it.explorer_url!)}>
                            <Icon name="link" size={16} />
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
