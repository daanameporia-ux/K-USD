import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Attempt } from "@shared/api";
import { formatUsdt, validateUsdt } from "@shared/format";
import { confirmAction, haptic, readClipboard } from "@shared/telegram";
import { AsyncButton, ErrorView, Loader, Notice, Row, StatusBadge, errorMessage, useLoad } from "@shared/ui";
import { api, type WithdrawConfig, type WithdrawPreview, type Withdrawal } from "../api";
import { TopBar } from "./common";

type Step = "network" | "address" | "amount" | "preview" | "done";
const ORDER: Step[] = ["network", "address", "amount", "preview", "done"];

function trimAmount(available: string): string {
  const [i, f = ""] = available.split(".");
  return i + "." + (f + "00").slice(0, 2);
}

export function Withdraw() {
  const nav = useNavigate();
  const cfg = useLoad(() => api.get<WithdrawConfig>("/api/client/withdrawals/config"));
  const [step, setStep] = useState<Step>("network");
  const [network, setNetwork] = useState<"TRC20" | "BEP20" | null>(null);
  const [address, setAddress] = useState("");
  const [amount, setAmount] = useState("");
  const [preview, setPreview] = useState<WithdrawPreview | null>(null);
  const [result, setResult] = useState<Withdrawal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const attempt = useRef(new Attempt());

  if (cfg.loading) return <Loader />;
  if (cfg.error || !cfg.data) return <ErrorView error={cfg.error} onRetry={() => cfg.reload()} />;
  const c = cfg.data;
  const net = c.networks.find((n) => n.network === network);
  const amountError = validateUsdt(amount);

  async function doPreview(value: string) {
    setError(null);
    try {
      const pv = await api.post<WithdrawPreview>("/api/client/withdrawals/preview", {
        network,
        address,
        amount: value.replace(",", "."),
      });
      // New parameters → a new attempt: the key of a failed earlier attempt is never sent with another request.
      if (!preview || preview.network !== pv.network || preview.address !== pv.address || preview.amount !== pv.amount) {
        attempt.current.done();
      }
      setPreview(pv);
      setStep("preview");
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function checkAddress() {
    setError(null);
    try {
      // the preview validates the address (format, contract, blacklist) before asking the amount
      await api.post<WithdrawPreview>("/api/client/withdrawals/preview", {
        network,
        address,
        amount: c.min_amount,
      });
      setStep("amount");
    } catch (e) {
      const msg = errorMessage(e);
      if (/адрес|контракт|нулев/i.test(msg)) setError(msg);
      else setStep("amount"); // amount-related errors are shown on the next step
    }
  }

  const progress = (
    <div className="steps" aria-hidden>
      {ORDER.slice(0, 4).map((s, i) => (
        <span key={s} className={ORDER.indexOf(step) >= i ? "on" : ""} />
      ))}
    </div>
  );

  if (!c.enabled) {
    return (
      <div>
        <TopBar title="Вывод" />
        <Notice tone="warn">Вывод временно приостановлен. Попробуйте позже.</Notice>
      </div>
    );
  }

  return (
    <div>
      <TopBar title="Вывод USDT" />
      {progress}
      {step === "network" && (
        <div className="card stack">
          <div className="muted small">
            Доступно к выводу: <b>{formatUsdt(c.available)}</b>, минимум {formatUsdt(c.min_amount)}
          </div>
          {c.networks.map((n) => (
            <button
              key={n.network}
              className="btn btn-secondary net"
              onClick={() => {
                setNetwork(n.network);
                setStep("address");
              }}
            >
              <span>{n.label}</span>
              <span className="small">комиссия {formatUsdt(n.fee)}</span>
            </button>
          ))}
        </div>
      )}

      {step === "address" && net && (
        <div className="card stack">
          <label className="field">
            <span>Адрес кошелька USDT в сети {net.label}</span>
            <textarea
              className="input mono"
              rows={2}
              value={address}
              spellCheck={false}
              autoCapitalize="off"
              onChange={(e) => setAddress(e.target.value.trim())}
              placeholder={net.network === "TRC20" ? "T…" : "0x…"}
            />
          </label>
          <button className="btn btn-ghost" onClick={async () => setAddress((await readClipboard()).trim())}>
            Вставить из буфера
          </button>
          {error && <Notice tone="bad">{error}</Notice>}
          <div className="btn-row">
            <button className="btn btn-secondary" onClick={() => setStep("network")}>
              Назад
            </button>
            <AsyncButton disabled={!address} onClick={checkAddress}>
              Далее
            </AsyncButton>
          </div>
        </div>
      )}

      {step === "amount" && net && (
        <div className="card stack">
          <label className="field">
            <span>Сумма списания, USDT (комиссия {formatUsdt(net.fee)} вычитается из суммы)</span>
            <input
              className="input input-big"
              inputMode="decimal"
              value={amount}
              placeholder="0.00"
              onChange={(e) => setAmount(e.target.value.replace(/[^\d.,]/g, ""))}
            />
            {amount && amountError && <div className="field-error">{amountError}</div>}
          </label>
          <button className="btn btn-ghost" onClick={() => setAmount(trimAmount(c.available))}>
            Вывести всё ({formatUsdt(c.available)})
          </button>
          {error && <Notice tone="bad">{error}</Notice>}
          <div className="btn-row">
            <button className="btn btn-secondary" onClick={() => setStep("address")}>
              Назад
            </button>
            <AsyncButton disabled={!amount || !!amountError} onClick={() => doPreview(amount)}>
              Далее
            </AsyncButton>
          </div>
        </div>
      )}

      {step === "preview" && preview && (
        <div className="card stack">
          <Row label="Сеть">{preview.network_label}</Row>
          <Row label="Адрес">
            <span className="mono small">{preview.address}</span>
          </Row>
          <Row label="Списывается">{formatUsdt(preview.amount)}</Row>
          <Row label="Комиссия">{formatUsdt(preview.fee)}</Row>
          <Row label="К получению">
            <b>{formatUsdt(preview.amount_to_send)}</b>
          </Row>
          <Notice tone="warn">
            Убедитесь, что адрес принадлежит сети {preview.network_label}. Перевод в другую сеть приведёт к потере
            средств.
          </Notice>
          {error && <Notice tone="bad">{error}</Notice>}
          <div className="btn-row">
            <button
              className="btn btn-secondary"
              onClick={() => {
                setError(null);
                setStep("amount");
              }}
            >
              Изменить
            </button>
            <AsyncButton
              onClick={async () => {
                if (!(await confirmAction(`Вывести ${preview.amount_to_send} USDT на ${preview.address}?`))) return;
                setError(null);
                try {
                  const body = { network: preview.network, address: preview.address, amount: preview.amount };
                  // same key only when resending exactly this request
                  const w = await api.post<Withdrawal>("/api/client/withdrawals", body, attempt.current.keyFor(body));
                  attempt.current.done();
                  haptic("success");
                  setResult(w);
                  setStep("done");
                } catch (e) {
                  setError(errorMessage(e));
                  haptic("error");
                }
              }}
            >
              Подтвердить
            </AsyncButton>
          </div>
        </div>
      )}

      {step === "done" && result && (
        <div className="card stack">
          <h2>Заявка {result.public_id} создана</h2>
          <StatusBadge status={result.status} label={result.status_label} />
          <p>
            К отправке {formatUsdt(result.amount_to_send)} ({result.network_label}). Средства заблокированы до
            исполнения. Пока заявка в очереди, её можно отменить.
          </p>
          {error && <Notice tone="bad">{error}</Notice>}
          {result.status === "pending" && (
            <AsyncButton
              variant="danger"
              onClick={async () => {
                if (!(await confirmAction("Отменить вывод?"))) return;
                try {
                  setResult(await api.post<Withdrawal>(`/api/client/withdrawals/${result.id}/cancel`));
                } catch (e) {
                  setError(errorMessage(e));
                }
              }}
            >
              Отменить вывод
            </AsyncButton>
          )}
          <button className="btn btn-secondary" onClick={() => nav("/")}>
            На главную
          </button>
        </div>
      )}
    </div>
  );
}
