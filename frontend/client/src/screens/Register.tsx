import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { AsyncButton, Notice, errorMessage } from "@shared/ui";
import { api } from "../api";

export function Register() {
  const nav = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  return (
    <div className="card stack">
      <h1>Регистрация</h1>
      <p>
        Здесь можно купить USDT за рубли по QR-коду СБП и вывести их на свой кошелёк в сети TRC-20 или BEP-20. Чтобы
        начать, пройдите короткую регистрацию.
      </p>
      {error && <Notice tone="bad">{error}</Notice>}
      {message && <Notice>{message}</Notice>}
      <AsyncButton
        className="btn-block"
        onClick={async () => {
          setError(null);
          try {
            const res = await api.post<{ step: { status: string; message: string | null } }>("/api/client/register");
            if (res.step.status === "passed") nav("/", { replace: true });
            else setMessage(res.step.message ?? "Регистрация отклонена. Обратитесь в поддержку.");
          } catch (e) {
            setError(errorMessage(e));
          }
        }}
      >
        Пройти регистрацию
      </AsyncButton>
    </div>
  );
}
