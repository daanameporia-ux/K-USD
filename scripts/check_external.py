#!/usr/bin/env python3
"""Manual check of external services: Rapira, TronGrid, BSC RPC.

Makes one live request to each, prints the parsed result and says whether the format matches what the
service expects. Run on the server:  python scripts/check_external.py
(inside docker: docker compose exec app python scripts/check_external.py)
Only the standard library is used, so it also runs outside the app's virtualenv.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from decimal import Decimal

RATE_URL = os.environ.get("RATE_SOURCE_URL", "https://api.rapira.net/open/market/rates")
TRON_URL = os.environ.get("TRON_API_URL", "https://api.trongrid.io").rstrip("/")
TRON_KEY = os.environ.get("TRON_API_KEY") or None
BSC_URLS = [u.strip() for u in os.environ.get(
    "BSC_RPC_URLS", "https://bsc-dataseed.binance.org,https://bsc-dataseed1.defibit.io").split(",") if u.strip()]
TRON_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
BSC_USDT = "0x55d398326f99059ff775485246999027b3197955"
TRANSFER = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

results: list[tuple[str, bool, str]] = []


def http(url: str, body: dict | None = None, headers: dict | None = None) -> object:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "kusd-check/1",
                                                           **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - fixed https URLs from config
        return json.loads(resp.read(), parse_float=Decimal)


def check(name: str, fn) -> None:  # type: ignore[no-untyped-def]
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    results.append((name, ok, detail))
    print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}")


def rapira() -> tuple[bool, str]:
    doc = http(RATE_URL)
    assert isinstance(doc, dict)
    item = next((x for x in doc.get("data", []) if x.get("symbol") == "USDT/RUB"), None)
    problems = []
    if doc.get("code") != 0:
        problems.append(f"code={doc.get('code')}")
    if doc.get("isWorking") != 1:
        problems.append(f"isWorking={doc.get('isWorking')}")
    if item is None:
        return False, "USDT/RUB not found"
    for field in ("askPrice", "bidPrice", "close"):
        if not isinstance(item.get(field), Decimal | int) or Decimal(item[field]) <= 0:
            problems.append(f"{field} invalid: {item.get(field)!r}")
    detail = f"askPrice={item.get('askPrice')} bidPrice={item.get('bidPrice')} close={item.get('close')}"
    return not problems, detail + ("; " + ", ".join(problems) if problems else "; format matches")


def tron() -> tuple[bool, str]:
    headers = {"TRON-PRO-API-KEY": TRON_KEY} if TRON_KEY else {}
    events = http(f"{TRON_URL}/v1/contracts/{TRON_USDT}/events?event_name=Transfer&limit=1&only_confirmed=true",
                  headers=headers)
    assert isinstance(events, dict) and events.get("data"), "no recent USDT transfers returned"
    tx = events["data"][0]["transaction_id"]
    info = http(f"{TRON_URL}/walletsolidity/gettransactioninfobyid", {"value": tx}, headers)
    tx_events = http(f"{TRON_URL}/v1/transactions/{tx}/events", headers=headers)
    assert isinstance(info, dict) and isinstance(tx_events, dict)
    problems = []
    if info.get("id") != tx:
        problems.append("gettransactioninfobyid: id mismatch")
    if (info.get("receipt") or {}).get("result") != "SUCCESS":
        problems.append(f"receipt.result={info.get('receipt')}")
    transfer = next((e for e in tx_events.get("data", []) if e.get("event_name") == "Transfer"), None)
    if transfer is None:
        problems.append("no Transfer event")
    else:
        for key in ("from", "to", "value"):
            if key not in (transfer.get("result") or {}):
                problems.append(f"Transfer.result.{key} missing")
        if transfer.get("contract_address") != TRON_USDT:
            problems.append(f"contract_address={transfer.get('contract_address')}")
    value = (transfer or {}).get("result", {}).get("value")
    detail = f"tx {tx[:16]}…, block {info.get('blockNumber')}, value {value}"
    return not problems, detail + ("; " + ", ".join(problems) if problems else "; format matches")


def bsc_one(url: str) -> tuple[bool, str]:
    def rpc(method: str, params: list) -> object:
        doc = http(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        assert isinstance(doc, dict)
        if doc.get("error"):
            raise RuntimeError(doc["error"])
        return doc["result"]

    head = int(rpc("eth_blockNumber", []), 16)
    block = rpc("eth_getBlockByNumber", [hex(head - 20), True])
    assert isinstance(block, dict)
    tx = next((t for t in block["transactions"] if (t.get("to") or "").lower() == BSC_USDT
               and t.get("input", "").startswith("0xa9059cbb")), None)
    if tx is None:
        return True, f"head {head}; no USDT transfer in block {head - 20} (try again)"
    receipt = rpc("eth_getTransactionReceipt", [tx["hash"]])
    assert isinstance(receipt, dict)
    problems = []
    if receipt.get("status") not in ("0x1", "0x0"):
        problems.append(f"status={receipt.get('status')}")
    logs = [lg for lg in receipt.get("logs", []) if lg.get("address", "").lower() == BSC_USDT
            and lg.get("topics", [""])[0].lower().removeprefix("0x") == TRANSFER]
    if not logs:
        problems.append("no USDT Transfer log")
    amount = Decimal(int(logs[0]["data"], 16)) / Decimal(10) ** 18 if logs else None
    detail = f"head {head}, tx {tx['hash'][:18]}…, amount {amount}, confirmations {head - int(receipt['blockNumber'], 16)}"
    return not problems, detail + ("; " + ", ".join(problems) if problems else "; format matches")


def main() -> int:
    check(f"Rapira {RATE_URL}", rapira)
    check(f"TronGrid {TRON_URL}", tron)
    for url in BSC_URLS:
        check(f"BSC RPC {url}", lambda url=url: bsc_one(url))
    failed = [r for r in results if not r[1]]
    print("\nAll external services respond in the expected format." if not failed
          else f"\n{len(failed)} check(s) failed — see above.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
