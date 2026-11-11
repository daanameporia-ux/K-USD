import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { insideTelegram } from "@shared/telegram";
import { Loader } from "@shared/ui";
import { login } from "./api";
import { Home } from "./screens/Home";
import { Buy } from "./screens/Buy";
import { OrderScreen } from "./screens/OrderScreen";
import { Withdraw } from "./screens/Withdraw";
import { History } from "./screens/History";
import { Register } from "./screens/Register";

export function App() {
  const [state, setState] = useState<"loading" | "ok" | "outside" | "failed">("loading");

  useEffect(() => {
    if (!insideTelegram()) {
      setState("outside");
      return;
    }
    login()
      .then((ok) => setState(ok ? "ok" : "failed"))
      .catch(() => setState("failed"));
  }, []);

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
