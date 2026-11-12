import { useMemo, useState } from "react";
import { ApiError } from "@shared/api";
import { useSearchParams } from "react-router-dom";
import { formatDateTime, formatRate } from "@shared/format";
import { confirmAction } from "@shared/telegram";
import { AsyncButton, ErrorView, Loader, Notice, Row, useLoad } from "@shared/ui";
import { adminPath, api, getPage } from "../api";
import { useAuth } from "../auth";
import {
  PageHeader,
  PagedView,
  ResponsiveList,
  Section,
  TextField,
  useAction,
  usePaged,
  type Column,
} from "../components";
import { formatDuration, settingToDraft, settingsDiff } from "../helpers";
import type { BlacklistEntry, RateView, SettingMeta, SettingType, SettingValue, SettingsResponse } from "../types";

const SWITCHES: { key: string; label: string; on: string; off: string }[] = [
  { key: "purchases_enabled", label: "Покупки", on: "Включить покупки USDT?", off: "Выключить покупки USDT для всех клиентов?" },
  { key: "withdrawals_enabled", label: "Выводы", on: "Включить выводы?", off: "Выключить выводы для всех клиентов?" },
];
const SWITCH_KEYS = new Set(SWITCHES.map((s) => s.key));

const RATE_FIELDS = [
  { value: "askPrice", label: "askPrice — цена продажи" },
  { value: "bidPrice", label: "bidPrice — цена покупки" },
  { value: "close", label: "close — последняя сделка" },
];

type Tab = "general" | "antifraud" | "rate" | "blacklist";
const TABS: { id: Tab; label: string }[] = [
  { id: "general", label: "Основные" },
  { id: "antifraud", label: "Антифрод" },
  { id: "rate", label: "Курс" },
  { id: "blacklist", label: "Чёрный список" },
];

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.id === params.get("tab"))?.id ?? "general") as Tab;
  return (
    <div className="page">
      <PageHeader title="Настройки" />
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={t.id === tab}
            className={`tab${t.id === tab ? " tab-active" : ""}`}
            onClick={() => setParams(t.id === "general" ? {} : { tab: t.id }, { replace: true })}
          >
            {t.label}
          </button>
        ))}
      </div>
      {(tab === "general" || tab === "antifraud") && <SettingsForm tab={tab} />}
      {tab === "rate" && <RateBlock />}
      {tab === "blacklist" && <Blacklist />}
    </div>
  );
}

// --- settings form ---------------------------------------------------------------------------

function SettingsForm({ tab }: { tab: "general" | "antifraud" }) {
  const { refreshMe } = useAuth();
  const { data, error, loading, reload, setData } = useLoad(
    () => api.get<SettingsResponse>(adminPath("/settings")),
    [],
  );
  const [draft, setDraft] = useState<Record<string, SettingValue> | null>(null);
  const [comment, setComment] = useState("");
  const action = useAction();

  const types = useMemo(() => {
    const t: Record<string, SettingType> = {};
    if (!data) return t;
    for (const g of data.meta.general) t[g.key] = g.type;
    for (const r of data.meta.rules) {
      if (r.toggle) t[r.toggle] = "bool";
      for (const p of r.params) t[p.key] = p.type;
    }
    return t;
  }, [data]);

  const base = useMemo(() => {
    const d: Record<string, SettingValue> = {};
    if (!data) return d;
    for (const key of Object.keys(types)) if (!SWITCH_KEYS.has(key)) d[key] = settingToDraft(data.values[key] ?? null);
    return d;
  }, [data, types]);

  if (loading && !data) return <Loader />;
  if (error && !data) return <ErrorView error={error} onRetry={() => void reload()} />;
  if (!data) return null;

  const current = draft ?? base;
  const changes = settingsDiff(data.values, current, types);
  const changedKeys = Object.keys(changes);
  const set = (key: string, value: SettingValue) => setDraft({ ...current, [key]: value });

  async function save() {
    if (changedKeys.length === 0) return;
    const ok = await action.run(async () => {
      const res = await api.patch<{ values: Record<string, SettingValue>; changed: string[] }>(adminPath("/settings"), {
        changes,
        comment: comment.trim() || null,
      });
      setData({ ...data!, values: res.values });
      setDraft(null);
      setComment("");
    }, "Настройки сохранены.");
    if (ok) await refreshMe();
  }

  async function toggleSwitch(key: string, on: boolean) {
    const sw = SWITCHES.find((s) => s.key === key)!;
    if (!(await confirmAction(on ? sw.on : sw.off))) return;
    const ok = await action.run(async () => {
      const res = await api.patch<{ values: Record<string, SettingValue> }>(adminPath("/settings"), {
        changes: { [key]: on },
      });
      setData({ ...data!, values: res.values });
    }, on ? `${sw.label} включены.` : `${sw.label} выключены.`);
    if (!ok) void reload(true);
  }

  const groups: { name: string; items: SettingMeta[] }[] = [];
  for (const g of data.meta.general) {
    if (SWITCH_KEYS.has(g.key)) continue;
    const isAf = g.group === "Антифрод";
    if (isAf !== (tab === "antifraud")) continue;
    let group = groups.find((x) => x.name === g.group);
    if (!group) groups.push((group = { name: g.group, items: [] }));
    group.items.push(g);
  }

  return (
    <>
      {tab === "general" && (
        <Section title="Глобальные рубильники">
          <div className="switches">
            {SWITCHES.map((sw) => {
              const on = Boolean(data.values[sw.key]);
              return (
                <div key={sw.key} className={`switch-card ${on ? "on" : "off"}`}>
                  <div>
                    <b>{sw.label}</b>{" "}
                    <span className={`badge badge-${on ? "ok" : "bad"}`}>{on ? "включены" : "выключены"}</span>
                  </div>
                  <AsyncButton variant={on ? "danger" : "primary"} onClick={() => toggleSwitch(sw.key, !on)}>
                    {on ? "Выключить" : "Включить"}
                  </AsyncButton>
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {groups.map((g) => (
        <Section key={g.name} title={g.name}>
          <div className="settings-fields">
            {g.items.map((m) => (
              <SettingInput key={m.key} meta={m} value={current[m.key]} changed={m.key in changes} onChange={set} />
            ))}
          </div>
        </Section>
      ))}

      {tab === "antifraud" && (
        <div className="rules">
          {data.meta.rules.map((r) => {
            const enabled = r.toggle ? Boolean(current[r.toggle]) : true;
            return (
              <div key={r.rule} className={`card rule-card${enabled ? "" : " rule-off"}`}>
                <div className="section-head">
                  <h3>
                    {r.rule}. {r.title}
                  </h3>
                  {r.toggle ? (
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={enabled}
                        onChange={(e) => set(r.toggle!, e.target.checked)}
                        aria-label={`${r.rule}: включено`}
                      />
                      <span>{enabled ? "включено" : "выключено"}</span>
                      {r.toggle in changes && <span className="badge badge-info">изменено</span>}
                    </label>
                  ) : (
                    <span className="badge badge-ok">всегда включено</span>
                  )}
                </div>
                <p className="muted small">{r.description}</p>
                {r.params.length > 0 && (
                  <div className="settings-fields">
                    {r.params.map((p) => (
                      <SettingInput
                        key={p.key}
                        meta={p}
                        value={current[p.key]}
                        changed={p.key in changes}
                        onChange={set}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <div className="save-bar card">
        {action.view}
        <div className="save-bar-row">
          <span className="muted small">
            {changedKeys.length ? `Изменено: ${changedKeys.length}` : "Изменений нет"}
          </span>
          <input
            className="input"
            placeholder="Комментарий для аудита (необязательно)"
            value={comment}
            maxLength={500}
            onChange={(e) => setComment(e.target.value)}
          />
          <div className="btn-row">
            <AsyncButton onClick={save} disabled={changedKeys.length === 0}>
              Сохранить
            </AsyncButton>
            {changedKeys.length > 0 && (
              <button className="btn btn-ghost" onClick={() => setDraft(null)}>
                Отменить
              </button>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function SettingInput({
  meta,
  value,
  changed,
  onChange,
}: {
  meta: SettingMeta;
  value: SettingValue;
  changed: boolean;
  onChange: (key: string, value: SettingValue) => void;
}) {
  const label = (
    <>
      {meta.label} {changed && <span className="badge badge-info">изменено</span>}
    </>
  );
  if (meta.type === "bool") {
    return (
      <label className="check">
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(meta.key, e.target.checked)} />
        <span>{label}</span>
      </label>
    );
  }
  if (meta.type === "choice" && meta.key === "rate_field") {
    return (
      <label className="field">
        <span>{label}</span>
        <select value={String(value ?? "")} onChange={(e) => onChange(meta.key, e.target.value)}>
          {RATE_FIELDS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
    );
  }
  return (
    <label className="field">
      <span>{label}</span>
      <input
        className="input"
        value={String(value ?? "")}
        inputMode={meta.type === "int" ? "numeric" : meta.type === "decimal" ? "decimal" : "text"}
        onChange={(e) => onChange(meta.key, e.target.value)}
      />
    </label>
  );
}

// --- rate ---------------------------------------------------------------------------------------

function RateBlock() {
  const { data: r, error, loading, reload, setData } = useLoad(() => api.get<RateView>(adminPath("/rate")), []);
  const [manual, setManual] = useState("");
  const action = useAction();

  if (loading && !r) return <Loader />;
  if (error && !r) return <ErrorView error={error} onRetry={() => void reload()} />;
  if (!r) return null;

  async function acceptJump() {
    // Show the freshest rejected rate and send its id: the server refuses if a newer fetch has replaced it meanwhile.
    let shown: RateView;
    try {
      shown = await api.get<RateView>(adminPath("/rate"));
    } catch (e) {
      action.setError(e instanceof ApiError ? e.message : "Не удалось получить курс.");
      return;
    }
    setData(shown);
    const pending = shown.last_fetch;
    if (!shown.pending_jump || !pending) {
      action.setError("Курса, ожидающего подтверждения, уже нет.");
      return;
    }
    if (!(await confirmAction(`Подтвердить скачок курса и применить ${formatRate(pending.value)}?`))) return;
    const ok = await action.run(
      async () => setData(await api.post<RateView>(adminPath("/rate/accept-jump"), { rate_id: pending.id })),
      "Курс применён.",
    );
    if (!ok) void reload();
  }

  async function setManualRate() {
    const value = manual.trim().replace(",", ".");
    if (!/^\d+(\.\d{1,2})?$/.test(value) || /^0+(\.0+)?$/.test(value)) {
      action.setError("Курс — положительное число, например 95.40.");
      return;
    }
    if (!(await confirmAction(`Установить ручной курс ${formatRate(value)}? Он заменит курс биржи.`))) return;
    const ok = await action.run(async () => {
      try {
        setData(await api.post<RateView>(adminPath("/rate/manual"), { rate: value }));
      } catch (e) {
        // The server compares the value with the market rate and asks to confirm a suspicious one (typo guard).
        const suspicious =
          e instanceof ApiError && (e.code === "manual_rate_deviates" || e.code === "manual_rate_unverified");
        if (!suspicious) throw e;
        if (!(await confirmAction(e.message))) throw new ApiError(0, "cancelled", "Ручной курс не изменён.");
        setData(await api.post<RateView>(adminPath("/rate/manual"), { rate: value, confirm: true }));
      }
    }, "Ручной курс установлен.");
    if (ok) setManual("");
  }

  async function clearManual() {
    if (!(await confirmAction("Убрать ручной курс и вернуться к курсу биржи?"))) return;
    await action.run(async () => setData(await api.del<RateView>(adminPath("/rate/manual"))), "Ручной курс снят.");
  }

  return (
    <div className="detail-grid">
      <Section
        title="Текущий курс"
        actions={
          <button className="btn btn-ghost btn-sm" onClick={() => void reload()}>
            Обновить
          </button>
        }
      >
        {r.current ? (
          <>
            <Row label="Курс">
              <b>{formatRate(r.current.value)}</b>
            </Row>
            <Row label="Источник">{r.current.source}</Row>
            <Row label="Возраст">{formatDuration(r.current.age_seconds)}</Row>
          </>
        ) : (
          <Notice tone="bad">Актуального курса нет — заявки не создаются.</Notice>
        )}
        <Row label="Поле Rapira">{r.rate_field}</Row>
        <Row label="Последний принятый">
          {r.last_accepted ? `${formatRate(r.last_accepted.value)} · ${formatDateTime(r.last_accepted.fetched_at)}` : "—"}
        </Row>
        {r.last_fetch && (
          <>
            <Row label="Последний запрос">{formatDateTime(r.last_fetch.fetched_at)}</Row>
            <Row label="Значение">
              {formatRate(r.last_fetch.value)}{" "}
              <span className={`badge badge-${r.last_fetch.accepted ? "ok" : "bad"}`}>
                {r.last_fetch.accepted ? "принят" : `отклонён${r.last_fetch.reject_reason ? `: ${r.last_fetch.reject_reason}` : ""}`}
              </span>
            </Row>
            <Row label="ask / bid">
              {formatRate(r.last_fetch.ask)} / {formatRate(r.last_fetch.bid)}
            </Row>
          </>
        )}
      </Section>
      <Section title="Управление курсом">
        <div className="stack">
          {action.view}
          {r.pending_jump && (
            <div className="stack">
              <Notice tone="warn">
                Новый курс {formatRate(r.last_fetch?.value ?? null)} отклонён защитой от скачка. Проверьте рынок и
                подтвердите, если значение верное.
              </Notice>
              <AsyncButton onClick={acceptJump}>Подтвердить скачок</AsyncButton>
            </div>
          )}
          <Row label="Ручной курс">{r.manual_rate ? <b>{formatRate(r.manual_rate)}</b> : "не задан"}</Row>
          <TextField label="Новый ручной курс, ₽ за 1 USDT" value={manual} onChange={setManual} inputMode="decimal" />
          <div className="btn-row">
            <AsyncButton onClick={setManualRate} disabled={!manual.trim()}>
              Установить ручной курс
            </AsyncButton>
            {r.manual_rate && (
              <AsyncButton variant="secondary" onClick={clearManual}>
                Снять ручной курс
              </AsyncButton>
            )}
          </div>
        </div>
      </Section>
    </div>
  );
}

// --- blacklist ------------------------------------------------------------------------------------

function Blacklist() {
  const paged = usePaged<BlacklistEntry>((cursor) => getPage<BlacklistEntry>("/blacklist", {}, cursor), []);
  const [network, setNetwork] = useState("TRC20");
  const [address, setAddress] = useState("");
  const [comment, setComment] = useState("");
  const action = useAction();

  async function add() {
    if (!address.trim()) {
      action.setError("Введите адрес.");
      return;
    }
    const ok = await action.run(async () => {
      const row = await api.post<BlacklistEntry>(adminPath("/blacklist"), {
        network,
        address: address.trim(),
        comment: comment.trim() || null,
      });
      paged.setItems((items) => [row, ...items]);
    }, "Адрес добавлен в чёрный список.");
    if (ok) {
      setAddress("");
      setComment("");
    }
  }

  async function remove(e: BlacklistEntry) {
    if (!(await confirmAction(`Удалить адрес ${e.address} из чёрного списка?`))) return;
    await action.run(async () => {
      await api.del(adminPath(`/blacklist/${e.id}`));
      paged.setItems((items) => items.filter((x) => x.id !== e.id));
    }, "Адрес удалён из чёрного списка.");
  }

  const columns: Column<BlacklistEntry>[] = [
    { title: "Адрес", render: (e) => <span className="mono">{e.address}</span> },
    { title: "Сеть", render: (e) => (e.network === "TRC20" ? "TRC-20" : "BEP-20") },
    { title: "Комментарий", render: (e) => e.comment ?? "—" },
    { title: "Добавлен", render: (e) => `${formatDateTime(e.created_at)}${e.created_by ? ` · сотрудник #${e.created_by}` : ""}` },
    {
      title: "",
      render: (e) => (
        <AsyncButton variant="danger" className="btn-sm" onClick={() => remove(e)}>
          Удалить
        </AsyncButton>
      ),
    },
  ];

  return (
    <>
      <Section title="Добавить адрес">
        <div className="stack">
          <div className="filters">
            <select value={network} onChange={(e) => setNetwork(e.target.value)} aria-label="Сеть">
              <option value="TRC20">TRC-20</option>
              <option value="BEP20">BEP-20</option>
            </select>
            <input
              className="input mono"
              placeholder="Адрес"
              value={address}
              maxLength={200}
              spellCheck={false}
              onChange={(e) => setAddress(e.target.value)}
            />
          </div>
          <TextField label="Комментарий" value={comment} onChange={setComment} maxLength={500} />
          {action.view}
          <AsyncButton onClick={add} disabled={!address.trim()}>
            Добавить
          </AsyncButton>
        </div>
      </Section>
      <PagedView paged={paged}>
        {(items) => (
          <ResponsiveList items={items} columns={columns} rowKey={(e) => e.id} emptyText="Чёрный список пуст." />
        )}
      </PagedView>
    </>
  );
}
