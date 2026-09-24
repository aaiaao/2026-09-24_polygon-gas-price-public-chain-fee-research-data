"""固定済みの新JPYC標本について、Polygon RPCからreceiptを取得して実費を集計する。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd


class RpcError(RuntimeError):
    """JSON-RPCの通信または応答エラー。"""


def _rpc_call(rpc_url: str, method: str, params: list[object], attempts: int = 4) -> object:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    request = Request(rpc_url, data=payload, headers={"Content-Type": "application/json", "User-Agent": "jpyc-fee-research/1.0"})
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=90) as response:  # noqa: S310 - RPC URL is the user's chosen endpoint.
                answer = json.load(response)
            if "error" in answer:
                raise RpcError(f"{method}: {answer['error']}")
            return answer["result"]
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RpcError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(1.5 * (attempt + 1))
    raise RpcError(f"RPC call failed after {attempts} attempts: {method}: {last_error}")


def _as_int(value: str | None) -> int | None:
    return None if value is None else int(value, 16)


def _load_completed(raw_path: Path) -> dict[str, dict[str, object]]:
    completed: dict[str, dict[str, object]] = {}
    if not raw_path.exists():
        return completed
    with raw_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                completed[str(record["transaction_hash"])] = record
    return completed


def _append_record(raw_path: Path, record: dict[str, object]) -> None:
    with raw_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _receipt_record(rpc_url: str, tx_hash: str) -> dict[str, object]:
    receipt = _rpc_call(rpc_url, "eth_getTransactionReceipt", [tx_hash])
    if receipt is None:
        raise RpcError(f"receiptが見つかりません: {tx_hash}")
    if not isinstance(receipt, dict):
        raise RpcError(f"receiptの形式を解釈できません: {tx_hash}")
    block = _rpc_call(rpc_url, "eth_getBlockByNumber", [receipt["blockNumber"], False])
    if not isinstance(block, dict):
        raise RpcError(f"blockの形式を解釈できません: {tx_hash}")
    return {
        "transaction_hash": tx_hash,
        "receipt_block_number": _as_int(receipt.get("blockNumber")),
        "gas_used": _as_int(receipt.get("gasUsed")),
        "effective_gas_price_wei": _as_int(receipt.get("effectiveGasPrice")),
        "transaction_status": _as_int(receipt.get("status")),
        "block_timestamp_unix": _as_int(block.get("timestamp")),
    }


def _build_fee_table(selected: pd.DataFrame, completed: dict[str, dict[str, object]], states: pd.DataFrame) -> pd.DataFrame:
    records = pd.DataFrame(completed.values())
    table = selected.merge(records, on="transaction_hash", how="left", validate="one_to_one")
    if table[["gas_used", "effective_gas_price_wei", "block_timestamp_unix"]].isna().any().any():
        raise ValueError("receipt取得が未完了です。再実行して完了させてください。")
    table["block_timestamp_utc"] = pd.to_datetime(table["block_timestamp_unix"], unit="s", utc=True)
    table["receipt_utc_date"] = table["block_timestamp_utc"].dt.normalize()
    table["gas_used"] = pd.to_numeric(table["gas_used"], errors="raise").astype("int64")
    table["effective_gas_price_wei"] = pd.to_numeric(table["effective_gas_price_wei"], errors="raise").astype("int64")
    table["effective_gas_price_gwei"] = table["effective_gas_price_wei"] / 1_000_000_000
    table["fee_pol"] = (
        table["gas_used"].astype(float)
        * table["effective_gas_price_wei"].astype(float)
        / 1_000_000_000_000_000_000
    )

    rates = states[["date", "pol_usd"]].copy()
    rates["date"] = pd.to_datetime(rates["date"], utc=True).dt.normalize()
    rates = rates.rename(columns={"date": "receipt_utc_date", "pol_usd": "pol_usd_daily"})
    table = table.merge(rates, on="receipt_utc_date", how="left", validate="many_to_one")
    table["fee_usd"] = table["fee_pol"] * pd.to_numeric(table["pol_usd_daily"], errors="coerce")
    table["date_assignment_matches_estimate"] = table["receipt_utc_date"].eq(pd.to_datetime(table["estimated_utc_date"], utc=True).dt.normalize())
    return table


def _summary(table: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for group, part in table.groupby("sample_group", sort=False):
        row: dict[str, object] = {"sample_group": group, "label": part["sample_group_label"].iloc[0], "n": len(part)}
        for measure in ("fee_pol", "fee_usd", "gas_used", "effective_gas_price_gwei"):
            values = part[measure].dropna()
            for stat, value in {
                "mean": values.mean(),
                "median": values.median(),
                "min": values.min(),
                "max": values.max(),
                "p25": values.quantile(0.25),
                "p75": values.quantile(0.75),
                "p95": values.quantile(0.95),
            }.items():
                row[f"{measure}_{stat}"] = value
        rows.append(row)
    return pd.DataFrame(rows)


def fetch_jpyc_sample_receipts(
    rpc_url: str,
    selected_csv: Path,
    states_csv: Path,
    out_dir: Path,
    pause_sec: float = 0.05,
) -> dict[str, object]:
    """receiptを再開可能に取得し、手数料の明細と状態別集計を保存する。"""
    selected = pd.read_csv(selected_csv)
    states = pd.read_csv(states_csv)
    required = {"transaction_hash", "sample_group", "sample_group_label", "estimated_utc_date"}
    missing = required - set(selected.columns)
    if missing:
        raise ValueError(f"selected_csvに必要な列がありません: {sorted(missing)}")
    if selected["transaction_hash"].duplicated().any():
        raise ValueError("selected_csvに重複するtransaction_hashがあります。")
    if "pol_usd" not in states.columns:
        raise ValueError("states_csvにpol_usd列がありません。")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "receipt_records.jsonl"
    completed = _load_completed(raw_path)
    todo = [tx_hash for tx_hash in selected["transaction_hash"] if tx_hash not in completed]
    print(f"Receipts: {len(completed):,}/{len(selected):,} complete; {len(todo):,} remaining")
    for index, tx_hash in enumerate(todo, start=1):
        record = _receipt_record(rpc_url, tx_hash)
        _append_record(raw_path, record)
        completed[tx_hash] = record
        if index % 25 == 0 or index == len(todo):
            print(f"Fetched {len(completed):,}/{len(selected):,}", flush=True)
        if pause_sec:
            time.sleep(pause_sec)

    fee_table = _build_fee_table(selected, completed, states)
    summary = _summary(fee_table)
    fees_path = out_dir / "jpyc_sample_receipt_fees.csv"
    summary_path = out_dir / "jpyc_fee_summary_by_state.csv"
    manifest_path = out_dir / "receipt_manifest.json"
    fee_table.to_csv(fees_path, index=False)
    summary.to_csv(summary_path, index=False)
    manifest = {
        "rpc_url": rpc_url,
        "calculation": "fee_pol = gas_used × effective_gas_price_wei ÷ 10^18; fee_usd = fee_pol × 日次POL/USD",
        "sample_count": int(len(selected)),
        "date_verification": "receiptのblock timestampでUTC日を確定。推定日との一致列を出力。",
        "files": {"raw_resumable": str(raw_path), "fee_table": str(fees_path), "summary": str(summary_path)},
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["files"]["manifest"] = str(manifest_path)
    print(f"Saved: {fees_path}")
    return {"fees": fee_table, "summary": summary, "manifest": manifest}


if __name__ == "__main__" and "ipykernel" not in sys.modules:
    raise SystemExit("Jupyterではrun_jpyc_receipt_fee_analysis.pyを%runしてください。")
