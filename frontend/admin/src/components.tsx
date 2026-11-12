/** Admin-only UI building blocks on top of @shared/ui. */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AsyncButton, Empty, ErrorView, Loader, Notice, errorMessage } from "@shared/ui";
import { haptic, openLink } from "@shared/telegram";
import type { Page } from "./types";

export const MOBILE_QUERY = "(max-width: 767px)";

export function useIsMobile(): boolean {
  const get = () => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia(MOBILE_QUERY).matches : false);
  const [mobile, setMobile] = useState(get);
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia(MOBILE_QUERY);
    const onChange = () => setMobile(mq.matches);
    onChange();
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);
  return mobile;
}

/**
 * Cursor-paginated list: first page on deps change, "Показать ещё" appends.
 * A non-silent reload (filter / tab change, «Обновить») drops the old rows first, so rows of the previous
 * filter are never shown under the new one — not while loading and not when the new request fails.
 */
export function usePaged<T>(fetchPage: (cursor: string | null) => Promise<Page<T>>, deps: unknown[]) {
  const [items, setItems] = useState<T[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const fnRef = useRef(fetchPage);
  fnRef.current = fetchPage;
  const gen = useRef(0);

  const reload = useCallback(async (silent = false) => {
    const my = ++gen.current;
    if (!silent) {
      setLoading(true);
      setItems([]);
      setCursor(null);
      setError(null);
    }
    try {
      const page = await fnRef.current(null);
      if (my !== gen.current) return;
      setItems(page.items);
      setCursor(page.next_cursor);
      setError(null);
    } catch (e) {
      if (my === gen.current) setError(e);
    } finally {
      if (my === gen.current) setLoading(false);
    }
  }, []);

  const loadMore = useCallback(async () => {
    if (!cursor) return;
    const my = gen.current;
    setLoadingMore(true);
    try {
      const page = await fnRef.current(cursor);
      if (my !== gen.current) return;
      setItems((prev) => [...prev, ...page.items]);
      setCursor(page.next_cursor);
    } catch (e) {
      if (my === gen.current) setError(e);
    } finally {
      setLoadingMore(false);
    }
  }, [cursor]);

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { items, setItems, loading, loadingMore, error, reload, loadMore, hasMore: cursor !== null };
}

export interface Column<T> {
  title: string;
  render: (item: T) => ReactNode;
  className?: string;
  /** Hide from mobile cards (e.g. already shown in the card header). */
  desktopOnly?: boolean;
}

/** Table on desktop, cards on mobile. The first column is the card heading. */
export function ResponsiveList<T>({
  items,
  columns,
  rowKey,
  link,
  emptyText,
}: {
  items: T[];
  columns: Column<T>[];
  rowKey: (item: T) => string | number;
  link?: (item: T) => string;
  emptyText: string;
}) {
  const mobile = useIsMobile();
  const navigate = useNavigate();
  const go = (e: { target: EventTarget }, to: string) => {
    if ((e.target as HTMLElement).closest("a,button,input,select,textarea")) return;
    navigate(to);
  };
  if (items.length === 0) return <Empty text={emptyText} />;
  if (mobile) {
    const [head, ...rest] = columns;
    return (
      <div className="list-cards">
        {items.map((item) => {
          const body = (
            <>
              <div className="list-card-head">{head.render(item)}</div>
              {rest
                .filter((c) => !c.desktopOnly)
                .map((c) => (
                  <div className="row" key={c.title}>
                    <span className="row-label">{c.title}</span>
                    <span className="row-value">{c.render(item)}</span>
                  </div>
                ))}
            </>
          );
          return (
            <div
              key={rowKey(item)}
              className={`card list-card${link ? " list-card-link" : ""}`}
              onClick={link ? (e) => go(e, link(item)) : undefined}
            >
              {body}
            </div>
          );
        })}
      </div>
    );
  }
  return (
    <div className="table-wrap card">
      <table className="table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.title} className={c.className}>
                {c.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr
              key={rowKey(item)}
              className={link ? "tr-link" : undefined}
              onClick={
                link
                  ? (e) => go(e, link(item))
                  : undefined
              }
            >
              {columns.map((c) => (
                <td key={c.title} className={c.className}>
                  {c.render(item)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Loading / error / list / "Показать ещё" for a usePaged result. */
export function PagedView<T>({
  paged,
  children,
}: {
  paged: ReturnType<typeof usePaged<T>>;
  children: (items: T[]) => ReactNode;
}) {
  if (paged.loading && paged.items.length === 0) return <Loader />;
  if (paged.error && paged.items.length === 0) return <ErrorView error={paged.error} onRetry={() => void paged.reload()} />;
  return (
    <>
      {children(paged.items)}
      {paged.error && paged.items.length > 0 ? <Notice tone="bad">{errorMessage(paged.error)}</Notice> : null}
      {paged.hasMore && (
        <div className="more">
          <AsyncButton variant="secondary" onClick={paged.loadMore} disabled={paged.loadingMore}>
            Показать ещё
          </AsyncButton>
        </div>
      )}
    </>
  );
}

/** Run an action, keep its error / success message for display. */
export function useAction() {
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const run = useCallback(async (fn: () => Promise<unknown>, success?: string): Promise<boolean> => {
    setError(null);
    setOk(null);
    try {
      await fn();
      if (success) setOk(success);
      haptic("success");
      return true;
    } catch (e) {
      setError(errorMessage(e));
      haptic("error");
      return false;
    }
  }, []);
  const clear = useCallback(() => {
    setError(null);
    setOk(null);
  }, []);
  const view = (
    <>
      {error && <Notice tone="bad">{error}</Notice>}
      {ok && <Notice tone="ok">{ok}</Notice>}
    </>
  );
  return { error, ok, run, clear, view, setError, setOk };
}

export function Section({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <div className="section-head">
        <h2>{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function PageHeader({ title, back, children }: { title: string; back?: string; children?: ReactNode }) {
  return (
    <div className="page-head">
      {back && (
        <Link to={back} className="back-link">
          ← Назад
        </Link>
      )}
      <div className="page-title">
        <h1>{title}</h1>
        {children}
      </div>
    </div>
  );
}

export function TextField({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  inputMode,
  autoComplete,
  maxLength,
  hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  inputMode?: "text" | "numeric" | "decimal";
  autoComplete?: string;
  maxLength?: number;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        className="input"
        type={type}
        value={value}
        placeholder={placeholder}
        inputMode={inputMode}
        autoComplete={autoComplete}
        maxLength={maxLength}
        onChange={(e) => onChange(e.target.value)}
      />
      {hint && <small className="muted small">{hint}</small>}
    </label>
  );
}

export function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="check">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

export function YesNo({ value, yes = "да", no = "нет" }: { value: boolean; yes?: string; no?: string }) {
  return <span className={`badge badge-${value ? "ok" : "muted"}`}>{value ? yes : no}</span>;
}

export function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => {
        e.preventDefault();
        openLink(href);
      }}
    >
      {children}
    </a>
  );
}
