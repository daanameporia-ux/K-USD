import { useState } from "react";
import { formatDateTime } from "@shared/format";
import { confirmAction } from "@shared/telegram";
import { AsyncButton, ErrorView, Loader, useLoad } from "@shared/ui";
import { adminPath, api } from "../api";
import { useMe } from "../auth";
import { PageHeader, ResponsiveList, Section, TextField, YesNo, useAction, type Column } from "../components";
import { staffTitle } from "../helpers";
import type { Page, Staff } from "../types";

const ROLES: Record<string, string> = { admin: "Админ", operator: "Оператор" };

export function StaffPage() {
  const me = useMe();
  const { data, error, loading, reload, setData } = useLoad(() => api.get<Page<Staff>>(adminPath("/staff")), []);
  const [who, setWho] = useState("");
  const [role, setRole] = useState<"operator" | "admin">("operator");
  const action = useAction();

  const replace = (s: Staff) => setData((d) => (d ? { ...d, items: d.items.map((x) => (x.id === s.id ? s : x)) } : d));

  async function add() {
    const raw = who.trim();
    let body: Record<string, unknown>;
    if (/^\d+$/.test(raw)) body = { tg_id: Number(raw), role };
    else if (/^@?[A-Za-z0-9_]{3,64}$/.test(raw)) body = { username: raw.replace(/^@/, ""), role };
    else {
      action.setError("Укажите числовой Telegram ID или @username.");
      return;
    }
    if (!(await confirmAction(`Добавить ${raw} с ролью «${ROLES[role]}»?`))) return;
    let invited = false;
    const ok = await action.run(async () => {
      const res = await api.post<{ staff: Staff; invited: boolean }>(adminPath("/staff"), body);
      invited = res.invited;
      setData((d) => (d ? { ...d, items: [...d.items.filter((x) => x.id !== res.staff.id), res.staff] } : d));
    });
    if (ok) {
      setWho("");
      action.setOk(
        invited
          ? "Приглашение создано на 48 часов: доступ появится, когда человек напишет боту /start."
          : "Сотрудник добавлен, доступ активен.",
      );
    }
  }

  async function changeRole(s: Staff, next: string) {
    if (next === s.role) return;
    if (!(await confirmAction(`Сменить роль ${staffTitle(s)} на «${ROLES[next]}»?`))) return;
    await action.run(
      async () => replace(await api.patch<Staff>(adminPath(`/staff/${s.id}`), { role: next })),
      "Роль изменена.",
    );
  }

  async function setActive(s: Staff, active: boolean) {
    const text = active ? `Активировать ${staffTitle(s)}?` : `Деактивировать ${staffTitle(s)}? Все его сессии будут отозваны.`;
    if (!(await confirmAction(text))) return;
    await action.run(
      async () => replace(await api.patch<Staff>(adminPath(`/staff/${s.id}`), { is_active: active })),
      active ? "Сотрудник активирован." : "Сотрудник деактивирован.",
    );
  }

  async function remove(s: Staff) {
    if (!(await confirmAction(`Удалить сотрудника ${staffTitle(s)}?`))) return;
    await action.run(async () => {
      await api.del(adminPath(`/staff/${s.id}`));
      setData((d) => (d ? { ...d, items: d.items.filter((x) => x.id !== s.id) } : d));
    }, "Сотрудник удалён.");
  }

  const columns: Column<Staff>[] = [
    {
      title: "Сотрудник",
      render: (s) => (
        <span className="id-cell">
          <b>{s.tg_username ? "@" + s.tg_username : s.tg_id ? `tg ${s.tg_id}` : `#${s.id}`}</b>
          {s.is_superadmin && <span className="badge badge-info">суперадмин</span>}
          {s.id === me.staff.id && <span className="badge badge-muted">это вы</span>}
          {!s.is_active && <span className="badge badge-bad">неактивен</span>}
        </span>
      ),
    },
    { title: "Telegram ID", render: (s) => s.tg_id ?? "—" },
    {
      title: "Роль",
      render: (s) =>
        s.is_superadmin || s.id === me.staff.id ? (
          ROLES[s.role]
        ) : (
          <select className="select-sm" value={s.role} onChange={(e) => void changeRole(s, e.target.value)} aria-label="Роль">
            <option value="operator">Оператор</option>
            <option value="admin">Админ</option>
          </select>
        ),
    },
    {
      title: "Приглашение",
      render: (s) =>
        s.invite_pending ? (
          <span className="badge badge-warn">ждёт /start до {formatDateTime(s.invite_expires_at)}</span>
        ) : (
          "—"
        ),
    },
    { title: "Веб-логин", render: (s) => s.login ?? "—" },
    { title: "TOTP", render: (s) => <YesNo value={s.totp_enabled} yes="включён" no="нет" /> },
    { title: "Последний вход", render: (s) => formatDateTime(s.last_login_at) },
    {
      title: "Действия",
      render: (s) =>
        s.is_superadmin || s.id === me.staff.id ? (
          <span className="muted small">—</span>
        ) : (
          <span className="btn-row nowrap">
            <AsyncButton variant="secondary" className="btn-sm" onClick={() => setActive(s, !s.is_active)}>
              {s.is_active ? "Деактивировать" : "Активировать"}
            </AsyncButton>
            <AsyncButton variant="danger" className="btn-sm" onClick={() => remove(s)}>
              Удалить
            </AsyncButton>
          </span>
        ),
    },
  ];

  return (
    <div className="page">
      <PageHeader title="Сотрудники" />
      <Section title="Добавить сотрудника">
        <div className="stack">
          <TextField
            label="Telegram ID или @username"
            value={who}
            onChange={setWho}
            maxLength={65}
            hint="По ID доступ появляется сразу. По @username создаётся приглашение на 48 часов: доступ появится, когда человек отправит боту /start."
          />
          <label className="field">
            <span>Роль</span>
            <select value={role} onChange={(e) => setRole(e.target.value as "operator" | "admin")}>
              <option value="operator">Оператор</option>
              <option value="admin">Админ</option>
            </select>
          </label>
          {action.view}
          <AsyncButton onClick={add} disabled={!who.trim()}>
            Добавить
          </AsyncButton>
        </div>
      </Section>
      {loading && !data ? (
        <Loader />
      ) : error && !data ? (
        <ErrorView error={error} onRetry={() => void reload()} />
      ) : (
        <ResponsiveList items={data?.items ?? []} columns={columns} rowKey={(s) => s.id} emptyText="Сотрудников нет." />
      )}
    </div>
  );
}
