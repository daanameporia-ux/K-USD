import { useState } from "react";
import { formatDateTime } from "@shared/format";
import { confirmAction } from "@shared/telegram";
import { AsyncButton, CopyButton, Notice, Row } from "@shared/ui";
import { adminPath, api } from "../api";
import { useAuth } from "../auth";
import { PageHeader, Section, TextField, useAction } from "../components";
import { staffTitle } from "../helpers";
import type { Staff, TotpSetup } from "../types";

export function ProfilePage() {
  const { me, refreshMe, logout } = useAuth();
  const s = me.staff;
  const inBrowser = me.auth_method !== "telegram";

  async function doLogout() {
    if (!(await confirmAction("Выйти из админки?"))) return;
    await logout();
  }

  return (
    <div className="page">
      <PageHeader title="Профиль" />
      <div className="detail-grid">
        <Section title="Сотрудник">
          <Row label="Аккаунт">{staffTitle(s)}</Row>
          <Row label="Telegram ID">{s.tg_id ?? "—"}</Row>
          <Row label="Роль">{me.is_admin ? "Админ" : "Оператор"}</Row>
          {s.is_superadmin && <Row label="Суперадмин">да</Row>}
          <Row label="Вход выполнен">{me.auth_method === "telegram" ? "через Telegram" : "по паролю в браузере"}</Row>
          <Row label="Веб-логин">{s.login ?? "не задан"}</Row>
          <Row label="TOTP">{s.totp_enabled ? "включён" : "не включён"}</Row>
          <Row label="Платёжный провайдер">{me.payment_provider}</Row>
          <Row label="Последний вход">{formatDateTime(s.last_login_at)}</Row>
          <div className="btn-row mt">
            <AsyncButton variant="danger" onClick={doLogout}>
              Выйти
            </AsyncButton>
          </div>
        </Section>
        <div>
          {inBrowser && (
            <Notice tone="info">
              Веб-доступ и TOTP настраиваются только в админке внутри Telegram. В браузере сервер отклонит эти действия.
            </Notice>
          )}
          <WebCredentials staff={s} onDone={refreshMe} />
          <Totp staff={s} onDone={refreshMe} />
        </div>
      </div>
    </div>
  );
}

function WebCredentials({ staff, onDone }: { staff: Staff; onDone: () => Promise<void> }) {
  const [login, setLogin] = useState(staff.login ?? "");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const action = useAction();
  const loginOk = /^[A-Za-z0-9._-]{3,64}$/.test(login.trim());
  const pwOk = password.length >= 12;
  const same = password === password2;

  async function save() {
    if (!loginOk) return action.setError("Логин: 3–64 символа, латиница, цифры, точка, дефис, подчёркивание.");
    if (!pwOk) return action.setError("Пароль должен быть не короче 12 символов.");
    if (!same) return action.setError("Пароли не совпадают.");
    const ok = await action.run(
      () => api.post<Staff>(adminPath("/profile/web-credentials"), { login: login.trim(), password }),
      "Логин и пароль сохранены.",
    );
    if (ok) {
      setPassword("");
      setPassword2("");
      await onDone();
    }
  }

  return (
    <Section title="Веб-доступ">
      <div className="stack">
        <p className="muted small">
          Логин и пароль для входа в админку из браузера. Пароль — минимум 12 символов. Для входа также нужен TOTP.
        </p>
        <TextField label="Логин" value={login} onChange={setLogin} autoComplete="username" maxLength={64} />
        <TextField
          label="Новый пароль"
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete="new-password"
          maxLength={200}
        />
        {password && !pwOk && <div className="field-error">Ещё {12 - password.length} симв. до минимума.</div>}
        <TextField
          label="Повторите пароль"
          type="password"
          value={password2}
          onChange={setPassword2}
          autoComplete="new-password"
          maxLength={200}
        />
        {password2 && !same && <div className="field-error">Пароли не совпадают.</div>}
        {action.view}
        <AsyncButton onClick={save} disabled={!loginOk || !pwOk || !same}>
          Сохранить
        </AsyncButton>
      </div>
    </Section>
  );
}

function Totp({ staff, onDone }: { staff: Staff; onDone: () => Promise<void> }) {
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [code, setCode] = useState("");
  const action = useAction();

  // The backend replaces the secret right away and turns TOTP off until the new code is confirmed.
  async function start() {
    if (
      staff.totp_enabled &&
      !(await confirmAction(
        "Перевыпустить TOTP? Старый код перестанет работать сразу, и до подтверждения нового кода вход в браузере будет недоступен.",
      ))
    )
      return;
    const ok = await action.run(async () => {
      setSetup(await api.post<TotpSetup>(adminPath("/profile/totp/setup")));
      setCode("");
    });
    if (ok) await onDone(); // the profile now shows TOTP as not enabled
  }

  async function cancel() {
    setSetup(null);
    setCode("");
    action.clear();
    await onDone(); // TOTP stays off until a new setup is confirmed
  }

  async function confirm() {
    const ok = await action.run(
      () => api.post<Staff>(adminPath("/profile/totp/confirm"), { code }),
      "TOTP включён.",
    );
    if (ok) {
      setSetup(null);
      setCode("");
      await onDone();
    }
  }

  return (
    <Section title="Двухфакторная защита (TOTP)">
      <div className="stack">
        <p className="muted small">
          {staff.totp_enabled
            ? "TOTP включён. Можно перевыпустить секрет, например при смене телефона."
            : "Без TOTP вход в браузере невозможен. Отсканируйте QR в Google Authenticator, Aegis или аналоге."}
        </p>
        {!setup && (
          <AsyncButton variant={staff.totp_enabled ? "secondary" : "primary"} onClick={start}>
            {staff.totp_enabled ? "Перевыпустить TOTP" : "Настроить TOTP"}
          </AsyncButton>
        )}
        {setup && (
          <div className="stack">
            <Notice tone="warn">
              Старый код уже не действует. Пока вы не подтвердите новый код, TOTP выключен и вход в админку из браузера
              недоступен.
            </Notice>
            <img className="qr" src={`data:image/png;base64,${setup.qr_png_base64}`} alt="QR-код для приложения-аутентификатора" />
            <div className="copy-block">
              <div className="muted small">Секрет (если QR не сканируется)</div>
              <div className="mono copy-value">{setup.secret}</div>
              <CopyButton value={setup.secret} label="Копировать секрет" />
            </div>
            <TextField
              label="Код из приложения"
              value={code}
              onChange={(v) => setCode(v.replace(/\D/g, "").slice(0, 6))}
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
            />
            <div className="btn-row">
              <AsyncButton onClick={confirm} disabled={!/^\d{6}$/.test(code)}>
                Подтвердить
              </AsyncButton>
              <AsyncButton variant="ghost" onClick={cancel}>
                Отмена
              </AsyncButton>
            </div>
          </div>
        )}
        {action.view}
      </div>
    </Section>
  );
}
