import { useState, type FormEvent } from "react";
import { Notice, errorMessage } from "@shared/ui";
import { passwordLogin } from "../api";
import { TextField } from "../components";

export function LoginForm({ message, onSuccess }: { message?: string; onSuccess: () => Promise<void> }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const totpOk = /^\d{6}$/.test(totp);
  const canSubmit = login.trim() !== "" && password !== "" && totpOk && !busy;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      await passwordLogin(login.trim(), password, totp);
      await onSuccess();
    } catch (err) {
      setError(errorMessage(err));
      setTotp("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center-screen">
      <form className="card auth-card stack" onSubmit={submit}>
        <h1>Вход в админку</h1>
        <p className="muted small">
          Логин и пароль задаются в админке внутри Telegram: «Профиль → Веб-доступ». Без включённого TOTP вход в
          браузере невозможен.
        </p>
        {message && <Notice tone="warn">{message}</Notice>}
        <TextField label="Логин" value={login} onChange={setLogin} autoComplete="username" maxLength={100} />
        <TextField
          label="Пароль"
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete="current-password"
          maxLength={200}
        />
        <TextField
          label="Код из приложения (6 цифр)"
          value={totp}
          onChange={(v) => setTotp(v.replace(/\D/g, "").slice(0, 6))}
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
        />
        {error && <Notice tone="bad">{error}</Notice>}
        <button type="submit" className="btn btn-primary btn-block" disabled={!canSubmit} aria-busy={busy}>
          {busy ? <span className="spinner spinner-sm" aria-hidden /> : null}
          Войти
        </button>
      </form>
    </div>
  );
}
