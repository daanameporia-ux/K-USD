import { useCallback, useEffect, useRef, useState, type ReactNode, type SetStateAction } from "react";
import { ApiError } from "./api";
import { copyText } from "./telegram";
import { statusTone } from "./format";

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return "Что-то пошло не так, попробуйте ещё раз.";
}

/**
 * Load data with loading / error states and a reload function.
 * Every reload() and setData() bumps a request counter; a response of an older reload is dropped, so a poll
 * started before an action can never overwrite the state the action put in (or a newer reload fetched).
 */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setDataState] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const seq = useRef(0);
  const reload = useCallback(async (silent = false) => {
    const my = ++seq.current;
    if (!silent) setLoading(true);
    try {
      const value = await fnRef.current();
      if (my !== seq.current) return;
      setDataState(value);
      setError(null);
    } catch (e) {
      if (my === seq.current) setError(e);
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, []);
  const setData = useCallback((value: SetStateAction<T | null>) => {
    seq.current += 1; // invalidate reloads still in flight
    setDataState(value);
    setError(null);
    setLoading(false);
  }, []);
  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading, reload, setData };
}

export function Loader({ text = "Загрузка…" }: { text?: string }) {
  return (
    <div className="state" role="status">
      <span className="spinner" aria-hidden />
      {text}
    </div>
  );
}

export function ErrorView({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <p>{errorMessage(error)}</p>
      {onRetry && (
        <button className="btn btn-secondary" onClick={onRetry}>
          Повторить
        </button>
      )}
    </div>
  );
}

export function Empty({ text }: { text: string }) {
  return <div className="state state-empty">{text}</div>;
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "bad" | "ok"; children: ReactNode }) {
  return <div className={`notice notice-${tone}`}>{children}</div>;
}

/** Button that blocks itself while its async handler runs. */
export function AsyncButton({
  onClick,
  children,
  variant = "primary",
  disabled,
  className = "",
  type = "button",
}: {
  onClick: () => Promise<unknown> | unknown;
  children: ReactNode;
  variant?: "primary" | "secondary" | "danger" | "ghost";
  disabled?: boolean;
  className?: string;
  type?: "button" | "submit";
}) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type={type}
      className={`btn btn-${variant} ${className}`}
      disabled={disabled || busy}
      aria-busy={busy}
      onClick={async () => {
        if (busy) return;
        setBusy(true);
        try {
          await onClick();
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? <span className="spinner spinner-sm" aria-hidden /> : null}
      {children}
    </button>
  );
}

export function StatusBadge({ status, label }: { status: string; label: string }) {
  return <span className={`badge badge-${statusTone(status)}`}>{label}</span>;
}

export function CopyButton({ value, label = "Копировать" }: { value: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="btn btn-ghost btn-sm"
      onClick={async () => {
        if (await copyText(value)) {
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        }
      }}
    >
      {done ? "Скопировано" : label}
    </button>
  );
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="row">
      <span className="row-label">{label}</span>
      <span className="row-value">{children}</span>
    </div>
  );
}
