"""Real TronGrid / BSC RPC responses (tests/fixtures/chains) and derived variants for each verify scenario."""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import httpx
import respx

DIR = Path(__file__).parent / "fixtures" / "chains"

TRON_TX = "bc4bce2be431169083788fc947b889b54f0b95a77f296676aadfd054ef495cb8"
TRON_TO = "TGfb53j2wY8J2F7J5RQhs6gdKpC2j6zsBY"
TRON_AMOUNT = Decimal("8.67")

BSC_TX = "0xd3b7027c6d7c1cfabff907837b90d78abccbacffa0a5ad09dddcdf5bd367f8ea"
BSC_TO = "0xB5c3809c0263244Eeb1948F223B0FF3bBEd695Ea"
BSC_AMOUNT_REAL = Decimal("39.13213074")
BSC_AMOUNT = Decimal("39.13")  # the receipt `data` is rewritten to a 2-decimals amount for withdrawal tests

TRON_API = "https://api.trongrid.io"
BSC_RPC1 = "https://bsc-dataseed.binance.org"
BSC_RPC2 = "https://bsc-dataseed1.defibit.io"


def load(name: str):
    return json.loads((DIR / name).read_text())


def tron_responses(scenario: str):
    info = load("tron_txinfo_ok.json")
    events = load("tron_events_ok.json")
    if scenario == "not_found":
        return load("tron_txinfo_notfound.json"), load("tron_events_notfound.json")
    if scenario == "tx_failed":
        info["receipt"]["result"] = "REVERT"
        info["result"] = "FAILED"
    elif scenario == "wrong_token":
        events["data"][0]["contract_address"] = "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj"
        info["log"][0]["address"] = "ea51342dabbb928ae1e576bd39eff8aaf070a8c6"
    elif scenario == "wrong_recipient":
        events["data"][0]["result"]["to"] = "0x" + "11" * 20
    elif scenario == "wrong_amount":
        events["data"][0]["result"]["value"] = "8670001"
    elif scenario == "logs_only":
        events = load("tron_events_notfound.json")
    return info, events


def mock_tron(router: respx.MockRouter, scenario: str, tx: str = TRON_TX) -> None:
    if scenario == "explorer_unavailable":
        router.post(f"{TRON_API}/walletsolidity/gettransactioninfobyid").mock(return_value=httpx.Response(503))
        return
    info, events = tron_responses(scenario)
    router.post(f"{TRON_API}/walletsolidity/gettransactioninfobyid").mock(return_value=httpx.Response(200, json=info))
    router.get(f"{TRON_API}/v1/transactions/{tx}/events").mock(return_value=httpx.Response(200, json=events))


def bsc_receipt(scenario: str):
    receipt = load("bsc_receipt_ok.json")
    log = receipt["result"]["logs"][0]
    log["data"] = "0x" + format(int(BSC_AMOUNT * 10**18), "064x")
    if scenario == "not_found":
        return load("bsc_receipt_notfound.json")
    if scenario == "tx_failed":
        receipt["result"]["status"] = "0x0"
    elif scenario == "wrong_token":
        log["address"] = "0xe9e7cea3dedca5984780bafc599bd69add087d56"
    elif scenario == "wrong_recipient":
        log["topics"][2] = "0x" + "0" * 24 + "11" * 20
    elif scenario == "wrong_amount":
        log["data"] = "0x" + format(int(BSC_AMOUNT * 10**18) + 1, "064x")
    elif scenario == "real":
        return load("bsc_receipt_ok.json")
    return receipt


def bsc_handler(scenario: str, head_offset: int | None = None):
    receipt = bsc_receipt(scenario)
    block = load("bsc_block.json")
    head = load("bsc_blocknumber.json")
    if head_offset is not None:
        head = copy.deepcopy(head)
        head["result"] = hex(int(receipt["result"]["blockNumber"], 16) + head_offset)

    def handler(request: httpx.Request) -> httpx.Response:
        method = json.loads(request.content)["method"]
        body = {"eth_getTransactionReceipt": receipt, "eth_blockNumber": head, "eth_getBlockByNumber": block}[method]
        return httpx.Response(200, json=body)

    return handler


def mock_bsc(router: respx.MockRouter, scenario: str) -> None:
    if scenario == "explorer_unavailable":
        router.post(BSC_RPC1).mock(return_value=httpx.Response(502))
        router.post(BSC_RPC2).mock(side_effect=httpx.ConnectError("refused"))
        return
    offset = 5 if scenario == "pending_confirmations" else None
    router.post(BSC_RPC1).mock(side_effect=bsc_handler(scenario, offset))
