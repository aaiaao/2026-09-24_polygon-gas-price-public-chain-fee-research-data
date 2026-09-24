"""Polygonの3局面を、全対象日を含む層化標本で宛先別gasUsedに集計する。

探索用の300ブロック標本とは別物。各対象日から重複なしで指定数のブロックを
抽出し、日境界はJSON-RPC上のブロック時刻で正確に求める。宛先はトップレベルの
``to`` であり、内部call先の帰属ではない。
"""

from __future__ import annotations

import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd


GROUPS = {
    "low_baseline": lambda row: row["fee_state"] == "low_baseline",
    "spike_day": lambda row: row["fee_state"] == "spike_day",
    "post_expansion_high_baseline": lambda row: (
        row["fee_state"] == "high_baseline"
        and float(row["gas_limit_level_millions"]) >= 160
        and float(row["block_time_level_sec"]) <= 1.5
    ),
}


def _rpc_call(rpc_url: str, method: str, params: list[object], attempts: int = 5) -> object:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    request = Request(rpc_url, data=body, headers={"Content-Type": "application/json", "User-Agent": "polygon-gas-research/1.0"})
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=90) as response:  # noqa: S310 - user chooses the public RPC.
                answer = json.load(response)
            if "error" in answer:
                raise RuntimeError(str(answer["error"]))
            return answer["result"]
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"RPC call failed: {method}: {last_error}")


def _first_block_at_or_after(rpc_url: str, target: pd.Timestamp, latest_block: int) -> int:
    """UTC日境界以降の最初のブロックを、RPCの時刻で二分探索する。"""
    target_unix = int(target.timestamp())
    low, high = 0, latest_block
    while low < high:
        middle = (low + high) // 2
        block = _rpc_call(rpc_url, "eth_getBlockByNumber", [hex(middle), False])
        if int(block["timestamp"], 16) < target_unix:
            low = middle + 1
        else:
            high = middle
    return low


def _build_exact_calendar(states: pd.DataFrame, rpc_url: str, cache_path: Path) -> pd.DataFrame:
    """各UTC日についてRPC時刻に基づく正確な開始・終了ブロックを作る（再開可能）。"""
    states = states.copy().sort_values("date").reset_index(drop=True)
    states["date"] = pd.to_datetime(states["date"], utc=True).dt.normalize()
    dates = list(states["date"])
    final_boundary = dates[-1] + pd.Timedelta(days=1)
    boundary_dates = dates + [final_boundary]
    cached: dict[str, int] = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    latest = int(_rpc_call(rpc_url, "eth_blockNumber", []), 16)
    for number, day in enumerate(boundary_dates, start=1):
        key = day.isoformat()
        if key not in cached:
            cached[key] = _first_block_at_or_after(rpc_url, day, latest)
            cache_path.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8")
        if number % 25 == 0 or number == len(boundary_dates):
            print(f"UTC日境界: {number:,}/{len(boundary_dates):,}", flush=True)
    states["start_block"] = [cached[day.isoformat()] for day in dates]
    states["end_block"] = [cached[(day + pd.Timedelta(days=1)).isoformat()] - 1 for day in dates]
    states["exact_block_count"] = states["end_block"] - states["start_block"] + 1
    if (states["exact_block_count"] <= 0).any():
        raise RuntimeError("日次ブロック範囲を構成できませんでした。")
    return states


def _make_fixed_sample(calendar: pd.DataFrame, blocks_per_day: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for phase, condition in GROUPS.items():
        eligible = calendar.loc[calendar.apply(condition, axis=1)].copy()
        for row in eligible.itertuples(index=False):
            available = int(row.end_block - row.start_block + 1)
            if available < blocks_per_day:
                raise ValueError(f"{row.date} のブロック数が不足しています: {available}")
            selected = rng.choice(np.arange(int(row.start_block), int(row.end_block) + 1), size=blocks_per_day, replace=False)
            rows.extend({
                "phase": phase,
                "target_utc_date": pd.Timestamp(row.date).isoformat(),
                "sampled_block_number": int(block_number),
                "blocks_per_day": blocks_per_day,
            } for block_number in selected)
    output = pd.DataFrame(rows).sort_values(["phase", "target_utc_date", "sampled_block_number"]).reset_index(drop=True)
    if output["sampled_block_number"].duplicated().any():
        raise RuntimeError("重複ブロックが作られました。")
    return output


def _completed_blocks(raw_path: Path) -> set[int]:
    if not raw_path.exists():
        return set()
    with raw_path.open(encoding="utf-8") as handle:
        return {int(json.loads(line)["sampled_block_number"]) for line in handle if line.strip()}


def _fetch_one(rpc_url: str, item: dict[str, object]) -> dict[str, object]:
    block_number = int(item["sampled_block_number"])
    block_hex = hex(block_number)
    block = _rpc_call(rpc_url, "eth_getBlockByNumber", [block_hex, True])
    receipts = _rpc_call(rpc_url, "eth_getBlockReceipts", [block_hex])
    if not isinstance(block, dict) or not isinstance(receipts, list):
        raise RuntimeError(f"ブロック応答を解釈できません: {block_number}")
    receipt_gas = {str(receipt["transactionHash"]).lower(): int(receipt["gasUsed"], 16) for receipt in receipts}
    recipients: dict[str, dict[str, int]] = {}
    for tx in block["transactions"]:
        tx_hash = str(tx["hash"]).lower()
        if tx_hash not in receipt_gas:
            raise RuntimeError(f"receiptが不足しています: {tx_hash}")
        recipient = (tx.get("to") or "contract_creation").lower()
        entry = recipients.setdefault(recipient, {"gas_used": 0, "transaction_count": 0})
        entry["gas_used"] += receipt_gas[tx_hash]
        entry["transaction_count"] += 1
    actual = pd.to_datetime(int(block["timestamp"], 16), unit="s", utc=True)
    target = pd.Timestamp(str(item["target_utc_date"]))
    if actual.normalize() != target.normalize():
        raise RuntimeError(f"UTC日境界の不一致: block={block_number}, expected={target.date()}, actual={actual.date()}")
    if sum(value["gas_used"] for value in recipients.values()) != int(block["gasUsed"], 16):
        raise RuntimeError(f"gasUsed合計がブロックと一致しません: {block_number}")
    return {
        "phase": item["phase"],
        "target_utc_date": str(item["target_utc_date"]),
        "sampled_block_number": block_number,
        "actual_block_timestamp_utc": actual.isoformat(),
        "block_gas_used": int(block["gasUsed"], 16),
        "transaction_count": len(block["transactions"]),
        "recipient_gas": recipients,
    }


def _summarise(raw_path: Path, detail_path: Path, summary_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    aggregates: dict[tuple[str, str], dict[str, int]] = {}
    phase_totals: dict[str, dict[str, int]] = {}
    with raw_path.open(encoding="utf-8") as input_handle, detail_path.open("w", newline="", encoding="utf-8") as output_handle:
        writer = csv.DictWriter(output_handle, fieldnames=["phase", "target_utc_date", "sampled_block_number", "recipient_address", "gas_used", "transaction_count", "block_gas_used"])
        writer.writeheader()
        for line in input_handle:
            if not line.strip():
                continue
            record = json.loads(line)
            phase = str(record["phase"])
            total = phase_totals.setdefault(phase, {"sampled_blocks": 0, "sampled_gas_used": 0, "sampled_transaction_count": 0})
            total["sampled_blocks"] += 1
            total["sampled_gas_used"] += int(record["block_gas_used"])
            total["sampled_transaction_count"] += int(record["transaction_count"])
            for address, values in record["recipient_gas"].items():
                writer.writerow({"phase": phase, "target_utc_date": record["target_utc_date"], "sampled_block_number": record["sampled_block_number"], "recipient_address": address, "gas_used": values["gas_used"], "transaction_count": values["transaction_count"], "block_gas_used": record["block_gas_used"]})
                entry = aggregates.setdefault((phase, address), {"gas_used": 0, "transaction_count": 0, "sampled_blocks": 0})
                entry["gas_used"] += int(values["gas_used"])
                entry["transaction_count"] += int(values["transaction_count"])
                entry["sampled_blocks"] += 1
    rows = []
    for (phase, address), values in aggregates.items():
        totals = phase_totals[phase]
        rows.append({"phase": phase, "recipient_address": address, **values, **totals, "gas_share_of_phase_sample": values["gas_used"] / totals["sampled_gas_used"]})
    summary = pd.DataFrame(rows).sort_values(["phase", "gas_used"], ascending=[True, False]).reset_index(drop=True)
    summary.to_csv(summary_path, index=False)
    totals = pd.DataFrame([{"phase": phase, **values} for phase, values in phase_totals.items()])
    return summary, totals


def run_stratified_recipient_gas_sample(
    rpc_url: str,
    states_csv: Path,
    out_dir: Path,
    blocks_per_day: int = 50,
    random_seed: int = 20_260_924,
    workers: int = 4,
) -> dict[str, object]:
    """全対象日の各日から指定数のブロックを標本化し、再開可能に取得する。"""
    if blocks_per_day <= 0 or workers <= 0:
        raise ValueError("blocks_per_dayとworkersは1以上にしてください。")
    states = pd.read_csv(states_csv)
    required = {"date", "fee_state", "gas_limit_level_millions", "block_time_level_sec"}
    if missing := required - set(states.columns):
        raise ValueError(f"状態CSVに必要な列がありません: {sorted(missing)}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    boundary_cache = out_dir / "utc_day_start_blocks.json"
    calendar_path = out_dir / "exact_daily_block_calendar.csv"
    selected_path = out_dir / "selected_blocks.csv"
    raw_path = out_dir / "sampled_block_recipient_gas.jsonl"
    detail_path = out_dir / "recipient_gas_by_sampled_block.csv"
    summary_path = out_dir / "recipient_gas_summary_by_phase.csv"
    manifest_path = out_dir / "manifest.json"

    if selected_path.exists():
        calendar = pd.read_csv(calendar_path)
        selected = pd.read_csv(selected_path)
        if int(selected["blocks_per_day"].iloc[0]) != blocks_per_day:
            raise ValueError("既存標本のblocks_per_dayが異なります。別out_dirを指定してください。")
        print("固定済みの層化標本を再利用します。")
    else:
        calendar = _build_exact_calendar(states, rpc_url, boundary_cache)
        calendar.to_csv(calendar_path, index=False)
        selected = _make_fixed_sample(calendar, blocks_per_day, random_seed)
        selected.to_csv(selected_path, index=False)
        print(f"固定済み層化標本: {len(selected):,} blocks")
        print(selected.groupby("phase").size().to_string())

    done = _completed_blocks(raw_path)
    todo = selected.loc[~selected["sampled_block_number"].isin(done)].to_dict("records")
    print(f"取得状況: {len(done):,}/{len(selected):,}; 残り {len(todo):,} blocks")
    if todo:
        with raw_path.open("a", encoding="utf-8") as handle, ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_fetch_one, rpc_url, item): item for item in todo}
            for completed_count, future in enumerate(as_completed(futures), start=1):
                result = future.result()
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                handle.flush()
                if completed_count % 100 == 0 or completed_count == len(todo):
                    print(f"取得済み: {len(done) + completed_count:,}/{len(selected):,}", flush=True)

    summary, totals = _summarise(raw_path, detail_path, summary_path)
    design = selected.groupby("phase", as_index=False).agg(target_days=("target_utc_date", "nunique"), blocks_per_day=("blocks_per_day", "first"), sampled_blocks=("sampled_block_number", "count"))
    manifest = {
        "method": f"全対象日の各UTC日から重複なしで{blocks_per_day}ブロックを無作為抽出。日境界はRPCのブロック時刻で確定。receipt gasUsedをトップレベル宛先別に集計。",
        "scope_limit": "全ブロックではなく、日次層化標本。宛先はトップレベル取引のtoであり、内部callの実行先帰属ではない。",
        "random_seed": random_seed,
        "workers": workers,
        "design": design.to_dict("records"),
        "files": {"calendar": str(calendar_path), "selected_blocks": str(selected_path), "raw_resumable": str(raw_path), "detail": str(detail_path), "summary": str(summary_path)},
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["files"]["manifest"] = str(manifest_path)
    return {"summary": summary, "phase_totals": totals, "design": design, "manifest": manifest}


if __name__ == "__main__" and "ipykernel" not in sys.modules:
    raise SystemExit("JupyterLabではrun_polygon_phase_recipient_gas_stratified.pyを%runしてください。")
