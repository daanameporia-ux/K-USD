import { Link } from "react-router-dom";
import { formatDateTime, formatRate, formatRub, formatUsdt } from "@shared/format";
import { ErrorView, Loader, useLoad } from "@shared/ui";
import { adminPath, api } from "../api";
import { PageHeader, ResponsiveList, Section } from "../components";
import { formatDuration, formatPct } from "../helpers";
import type { Dashboard, DayStats, PeriodStats } from "../types";
import { dayColumns } from "./UserDetail";

const PERIODS: { key: string; label: string }[] = [
  { key: "today", label: "Сегодня" },
  { key: "yesterday", label: "Вчера" },
  { key: "7d", label: "7 дней" },
  { key: "30d", label: "30 дней" },
];

const METRICS: { label: string; render: (p: PeriodStats) => string }[] = [
  { label: "Оборот", render: (p) => formatRub(p.turnover_rub) },
  { label: "Выдано USDT", render: (p) => formatUsdt(p.usdt_issued) },
  { label: "Маржа", render: (p) => formatRub(p.margin_rub) },
  { label: "Заявок создано", render: (p) => String(p.orders.created) },
  { label: "Оплачено", render: (p) => String(p.orders.paid) },
  { label: "Конверсия", render: (p) => formatPct(p.conversion_pct) },
  { label: "Новые клиенты", render: (p) => String(p.new_clients) },
  { label: "Активные клиенты", render: (p) => String(p.active_clients) },
  { label: "Выводов исполнено", render: (p) => `${p.withdrawals_completed} · ${formatUsdt(p.withdrawals_completed_sum)}` },
  { label: "Комиссий собрано", render: (p) => formatUsdt(p.fees_collected) },
];

export function DashboardPage() {
  const dash = useLoad(() => api.get<Dashboard>(adminPath("/dashboard")), []);
  const daily = useLoad(() => api.get<{ items: DayStats[] }>(adminPath("/dashboard/daily", { days: 30 })), []);
  const d = dash.data;

  return (
    <div className="page">
      <PageHeader title="Дашборд">
        <button
          className="btn btn-ghost btn-sm"
          disabled={dash.loading}
          onClick={() => {
            void dash.reload();
            void daily.reload();
          }}
        >
          Обновить
        </button>
      </PageHeader>
      {dash.loading && !d ? (
        <Loader />
      ) : dash.error && !d ? (
        <ErrorView error={dash.error} onRetry={() => void dash.reload()} />
      ) : d ? (
        <>
          <div className="tiles">
            <div className="card tile">
              <div className="muted small">Очередь выводов</div>
              <div className="tile-value">{d.queue.count}</div>
              <div className="small">{formatUsdt(d.queue.sum)}</div>
            </div>
            <div className="card tile">
              <div className="muted small">Обязательства</div>
              <div className="tile-value">{formatUsdt(d.liabilities.balance)}</div>
              <div className="small">в холде {formatUsdt(d.liabilities.hold)}</div>
            </div>
            <div className="card tile">
              <div className="muted small">Курс</div>
              <div className="tile-value">{d.rate ? formatRate(d.rate.value) : "нет"}</div>
              <div className="small">
                {d.rate ? `${d.rate.source} · ${formatDuration(d.rate.age_seconds)} назад` : "курс недоступен"}
              </div>
            </div>
            <div className="card tile">
              <div className="muted small">Антифрод</div>
              <div className="tile-value">
                <Link to="/users?filter=af_blocked">{d.af_blocked}</Link> /{" "}
                <Link to="/users?filter=suspicious">{d.suspicious}</Link>
              </div>
              <div className="small">заблокированы / подозрительные</div>
            </div>
          </div>
          <p className="muted small">Последний запрос курса: {formatDateTime(d.last_rate_fetched_at)}</p>
          <Section title="По периодам">
            <div className="table-wrap">
              <table className="table table-compact">
                <thead>
                  <tr>
                    <th>Показатель</th>
                    {PERIODS.map((p) => (
                      <th key={p.key} className="num">
                        {p.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {METRICS.map((m) => (
                    <tr key={m.label}>
                      <td>{m.label}</td>
                      {PERIODS.map((p) => (
                        <td key={p.key} className="num">
                          {d.periods[p.key] ? m.render(d.periods[p.key]) : "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
          <p className="muted small">Время — МСК; «7 дней» и «30 дней» включают сегодня.</p>
        </>
      ) : null}

      <h2 className="section-title">По дням</h2>
      {daily.loading && !daily.data ? (
        <Loader />
      ) : daily.error && !daily.data ? (
        <ErrorView error={daily.error} onRetry={() => void daily.reload()} />
      ) : (
        <ResponsiveList
          items={daily.data?.items ?? []}
          columns={dayColumns}
          rowKey={(x) => x.day}
          emptyText="Данных пока нет."
        />
      )}
    </div>
  );
}
