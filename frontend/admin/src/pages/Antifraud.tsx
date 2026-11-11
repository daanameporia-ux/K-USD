import { Link, useSearchParams } from "react-router-dom";
import { formatDateTime } from "@shared/format";
import { confirmAction } from "@shared/telegram";
import { AsyncButton } from "@shared/ui";
import { adminPath, api, getPage } from "../api";
import { useMe } from "../auth";
import { Check, PageHeader, PagedView, ResponsiveList, useAction, usePaged, type Column } from "../components";
import { compactJson } from "../helpers";
import type { AfEvent } from "../types";

const SEVERITY: Record<string, { label: string; tone: string }> = {
  info: { label: "инфо", tone: "info" },
  suspicious: { label: "подозрение", tone: "warn" },
  block: { label: "блокировка", tone: "bad" },
};

export function SeverityBadge({ severity }: { severity: string }) {
  const s = SEVERITY[severity] ?? { label: severity, tone: "muted" };
  return <span className={`badge badge-${s.tone}`}>{s.label}</span>;
}

export function afColumns(opts: { withUser: boolean; resolve?: (e: AfEvent) => Promise<unknown> }): Column<AfEvent>[] {
  const cols: Column<AfEvent>[] = [
    {
      title: "Правило",
      render: (e) => (
        <span className="id-cell">
          <b>{e.rule}</b>
          <SeverityBadge severity={e.severity} />
          {e.resolved_at && <span className="badge badge-ok">решено</span>}
        </span>
      ),
    },
  ];
  if (opts.withUser)
    cols.push({ title: "Клиент", render: (e) => <Link to={`/users/${e.user_id}`}>id {e.user_id}</Link> });
  cols.push(
    { title: "Детали", render: (e) => <span className="mono small">{compactJson(e.details, 200)}</span> },
    { title: "Когда", render: (e) => formatDateTime(e.created_at) },
    {
      title: "Решено",
      render: (e) =>
        e.resolved_at ? (
          `${formatDateTime(e.resolved_at)}${e.resolved_by ? ` · #${e.resolved_by}` : ""}`
        ) : opts.resolve ? (
          <AsyncButton variant="secondary" className="btn-sm" onClick={() => opts.resolve!(e)}>
            Отметить решённым
          </AsyncButton>
        ) : (
          "—"
        ),
    },
  );
  return cols;
}

export function AntifraudPage() {
  const me = useMe();
  const [params, setParams] = useSearchParams();
  const showResolved = params.get("resolved") === "all";
  const action = useAction();
  const paged = usePaged<AfEvent>(
    (cursor) => getPage<AfEvent>("/antifraud/events", { resolved: showResolved ? undefined : false }, cursor),
    [showResolved],
  );

  async function resolve(e: AfEvent) {
    if (!(await confirmAction(`Отметить событие ${e.rule} клиента id ${e.user_id} решённым?`))) return;
    await action.run(async () => {
      const updated = await api.post<AfEvent>(adminPath(`/antifraud/events/${e.id}/resolve`));
      paged.setItems((items) =>
        showResolved ? items.map((x) => (x.id === e.id ? updated : x)) : items.filter((x) => x.id !== e.id),
      );
    }, "Событие отмечено решённым.");
  }

  return (
    <div className="page">
      <PageHeader title="Антифрод">
        <button className="btn btn-ghost btn-sm" onClick={() => void paged.reload()} disabled={paged.loading}>
          Обновить
        </button>
      </PageHeader>
      <Check
        label="Показывать решённые"
        checked={showResolved}
        onChange={(v) => setParams(v ? { resolved: "all" } : {}, { replace: true })}
      />
      {action.view}
      <PagedView paged={paged}>
        {(items) => (
          <ResponsiveList
            items={items}
            columns={afColumns({ withUser: true, resolve: me.is_admin ? resolve : undefined })}
            rowKey={(e) => e.id}
            emptyText={showResolved ? "Событий нет." : "Нерешённых событий нет."}
          />
        )}
      </PagedView>
    </div>
  );
}
