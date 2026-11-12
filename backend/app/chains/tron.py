"""TRC-20 USDT verification via TronGrid."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from app.chains.addresses import TRON_USDT_CONTRACT, tron_from_hex
from app.chains.base import NOT_FOUND, TRANSFER_TOPIC, ChainUnavailable, Transfer, TransferInfo, pick_transfer

DECIMALS = Decimal(10) ** 6


class TronVerifier:
    usdt_contract = TRON_USDT_CONTRACT
    required_confirmations = 1  # present in the solidity node == confirmed

    def __init__(self, http: httpx.AsyncClient, api_url: str, api_key: str | None = None) -> None:
        self._http = http
        self._base = api_url.rstrip("/")
        self._headers = {"TRON-PRO-API-KEY": api_key} if api_key else {}

    def explorer_url(self, tx_hash: str) -> str:
        return f"https://tronscan.org/#/transaction/{tx_hash}"

    async def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        try:
            resp = await self._http.request(
                method, self._base + path, json=json, headers=self._headers, timeout=httpx.Timeout(15.0, connect=5.0)
            )
        except httpx.HTTPError as exc:
            raise ChainUnavailable(f"TronGrid: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise ChainUnavailable(f"TronGrid HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ChainUnavailable("TronGrid returned invalid JSON") from exc

    async def get_usdt_transfer(self, tx_hash: str, expected_to: str) -> TransferInfo:
        info = await self._request("POST", "/walletsolidity/gettransactioninfobyid", {"value": tx_hash})
        if not isinstance(info, dict):
            raise ChainUnavailable("unexpected gettransactioninfobyid response")
        if not info or not info.get("id"):
            return NOT_FOUND
        try:
            receipt = info.get("receipt") or {}
            # TRC-20 calls always carry receipt.result; anything but SUCCESS (or a missing field) is a failure
            success = receipt.get("result") == "SUCCESS" and info.get("result") != "FAILED"
            block_time = datetime.fromtimestamp(int(info["blockTimeStamp"]) / 1000, tz=UTC)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
            # never treat an incomplete answer as a pass: retry later
            raise ChainUnavailable(f"incomplete gettransactioninfobyid response: {type(exc).__name__}") from exc

        transfers = await self._events(tx_hash)
        if not transfers:
            transfers = self._from_logs(info.get("log") or [])
        chosen = pick_transfer(transfers, self.usdt_contract, expected_to, case_insensitive=False)
        return TransferInfo(
            found=True,
            success=success,
            confirmations=1,
            token_contract=chosen.contract if chosen else None,
            to_address=chosen.to_address if chosen else None,
            amount=chosen.amount if chosen else None,
            block_time=block_time,
            raw={"info": info},
        )

    async def _events(self, tx_hash: str) -> list[Transfer]:
        doc = await self._request("GET", f"/v1/transactions/{tx_hash}/events")
        if not isinstance(doc, dict) or not isinstance(doc.get("data"), list):
            raise ChainUnavailable("unexpected events response")
        out: list[Transfer] = []
        for ev in doc["data"]:
            if not isinstance(ev, dict) or ev.get("event_name") != "Transfer":
                continue
            result = ev.get("result") or {}
            try:
                contract = str(ev["contract_address"])
                if not contract.startswith("T"):
                    contract = tron_from_hex(contract)
                to = str(result["to"])
                to = to if to.startswith("T") else tron_from_hex(to)
                amount = Decimal(str(result["value"])) / DECIMALS
            except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
                continue
            out.append(Transfer(contract, to, amount))
        return out

    @staticmethod
    def _from_logs(logs: list[Any]) -> list[Transfer]:
        out: list[Transfer] = []
        for log in logs:
            try:
                topics = log["topics"]
                if len(topics) < 3 or topics[0].lower().removeprefix("0x") != TRANSFER_TOPIC:
                    continue
                contract = tron_from_hex(str(log["address"]))
                to = tron_from_hex(topics[2][-40:])
                amount = Decimal(int(str(log["data"]), 16)) / DECIMALS
            except (KeyError, ValueError, TypeError, IndexError, AttributeError, ArithmeticError):
                continue
            out.append(Transfer(contract, to, amount))
        return out
