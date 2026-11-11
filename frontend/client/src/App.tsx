import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { insideTelegram } from "@shared/telegram";
import { ApiError } from "@shared/api";
import { Loader } from "@shared/ui";
import { login } from "./api";
import { Home } from "./screens/Home";
import { Buy } from "./screens/Buy";
import { OrderScreen } from "./screens/OrderScreen";
import { Withdraw } from "./screens/Withdraw";
import { History } from "./screens/History";
import { Register } from "./screens/Register";

export function App() {
  const [state, setState] = useState<"loading" | "ok" | "outside" | "failed" | "offline">("loading");

  const start = () => {
    if (!insideTelegram()) {
      setState("outside");
      return;
    }
    setState("loading");
    login()
      .then((ok) => setState(ok ? "ok" : "failed"))
      .catch((e) => setState(e instanceof ApiError && e.code === "network_error" ? "offline" : "failed"));
  };

  useEffect(start, []);

  if (state === "loading") return <Loader text="Вход…" />;
  if (state === "outside")
    return (
      <div className="page">
        <div className="card">
          <h1>Откройте приложение в Telegram</h1>
          <p className="muted">Это мини-приложение работает внутри Telegram: откройте бота и нажмите «Приложение».</p>
        </div>
      </div>
    );
  if (state === "offline")
    return (
      <div className="page">
        <div className="card stack">
          <h1>Нет связи с сервером</h1>
          <p className="muted">Проверьте интернет и попробуйте ещё раз.</p>
          <button className="btn btn-primary btn-block" onClick={start}>
            Повторить
          </button>
        </div>
      </div>
    );
  if (state === "failed")
    return (
      <div className="page">
        <div className="card">
          <h1>Не удалось войти</h1>
          <p className="muted">Закройте и откройте приложение заново.</p>
        </div>
      </div>
    );
  return (
    <div className="page">
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/register" element={<Register />} />
        <Route path="/buy" element={<Buy />} />
        <Route path="/order/:id" element={<OrderScreen />} />
        <Route path="/withdraw" element={<Withdraw />} />
        <Route path="/history" element={<History />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
