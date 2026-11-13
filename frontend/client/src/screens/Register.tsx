import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { AsyncButton, Notice, errorMessage } from "@shared/ui";
import { api } from "../api";
import { Bubble, Footer, Icon } from "./common";

const FEATURES = [
  { icon: "qr", title: "Оплата по QR-коду СБП", text: "Из приложения любого банка" },
  { icon: "wallet", title: "USDT на вашем балансе", text: "Зачисление сразу после оплаты" },
  { icon: "up", title: "Вывод на свой кошелёк", text: "Сети TRC-20 и BEP-20" },
] as const;

export function Register() {
  const nav = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  return (
    <div className="screen">
      <div className="welcome">
        <div className="welcome-mark">
          <Icon name="bolt" size={40} />
        </div>
        <h1>Покупка USDT за рубли</h1>
        <p className="muted">Чтобы начать, пройдите короткую регистрацию.</p>
      </div>
      <div className="card list features">
        {FEATURES.map((f) => (
          <div key={f.title} className="tile" style={{ cursor: "default" }}>
            <Bubble icon={f.icon} />
            <span className="tile-body">
              <span className="tile-title">{f.title}</span>
              <div className="tile-sub">{f.text}</div>
            </span>
          </div>
        ))}
      </div>
      {error && <Notice tone="bad">{error}</Notice>}
      {message && <Notice>{message}</Notice>}
      <Footer>
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
      </Footer>
    </div>
  );
}
