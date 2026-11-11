/** Session state: Telegram initData login inside Telegram, login/password/TOTP form in a browser. */

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { insideTelegram } from "@shared/telegram";
import { ErrorView, Loader } from "@shared/ui";
import { ApiError, adminPath, api, logout as apiLogout, setUnauthorizedHandler, telegramLogin } from "./api";
import { LoginForm } from "./pages/Login";
import type { Me } from "./types";

type State =
  | { kind: "booting" }
  | { kind: "login"; message?: string }
  | { kind: "denied"; message: string }
  | { kind: "signed_out" }
  | { kind: "error"; error: unknown }
  | { kind: "ready"; me: Me };

interface AuthCtx {
  me: Me;
  refreshMe: () => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthCtx | null>(null);

export function useAuth(): AuthCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}

export function useMe(): Me {
  return useAuth().me;
}

function NoAccess({ message }: { message: string }) {
  return (
    <div className="center-screen">
      <div className="card auth-card">
        <h1>Нет доступа</h1>
        <p>{message}</p>
        <p className="muted small">Если вы сотрудник, попросите администратора добавить ваш Telegram-аккаунт.</p>
      </div>
    </div>
  );
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ kind: "booting" });

  const loadMe = useCallback(async () => {
    const me = await api.get<Me>(adminPath("/me"));
    setState({ kind: "ready", me });
  }, []);

  const start = useCallback(async () => {
    setState({ kind: "booting" });
    if (!insideTelegram()) {
      setState({ kind: "login" });
      return;
    }
    try {
      await telegramLogin();
      await loadMe();
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) setState({ kind: "denied", message: e.message });
      else setState({ kind: "error", error: e });
    }
  }, [loadMe]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      api.setToken(null);
      if (insideTelegram()) setState({ kind: "error", error: new ApiError(401, "unauthorized", "Сессия истекла. Откройте админку заново.") });
      else setState({ kind: "login", message: "Сессия истекла, войдите снова." });
    });
    void start();
  }, [start]);

  const refreshMe = useCallback(async () => {
    try {
      const me = await api.get<Me>(adminPath("/me"));
      setState((s) => (s.kind === "ready" ? { kind: "ready", me } : s));
    } catch {
      /* keep the previous profile */
    }
  }, []);

  const logout = useCallback(async () => {
    await apiLogout();
    setState(insideTelegram() ? { kind: "signed_out" } : { kind: "login" });
  }, []);

  switch (state.kind) {
    case "booting":
      return (
        <div className="center-screen">
          <Loader text="Вход…" />
        </div>
      );
    case "denied":
      return <NoAccess message={state.message} />;
    case "error":
      return (
        <div className="center-screen">
          <div className="card auth-card">
            <ErrorView error={state.error} onRetry={() => void start()} />
          </div>
        </div>
      );
    case "signed_out":
      return (
        <div className="center-screen">
          <div className="card auth-card stack">
            <h1>Вы вышли</h1>
            <button className="btn btn-primary btn-block" onClick={() => void start()}>
              Войти снова
            </button>
          </div>
        </div>
      );
    case "login":
      return <LoginForm message={state.message} onSuccess={loadMe} />;
    case "ready":
      return <Ctx.Provider value={{ me: state.me, refreshMe, logout }}>{children}</Ctx.Provider>;
  }
}
