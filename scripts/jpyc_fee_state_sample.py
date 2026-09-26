"""新JPYCの通常送金を、Polygon全体のガス価格状態ごとに標本化する。

この段階ではreceiptを取りに行かない。先に「どの局面の、どの取引を比べるか」を
固定し、次段で selected_transactions.csv の receipt を取得する。

Notebookセルでの実行例:

    from pathlib import Path
    import sys, importlib

    SCRIPT_DIR = Path.cwd() / "scripts"
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    import jpyc_fee_state_sample
    importlib.reload(jpyc_fee_state_sample)

    result = jpyc_fee_state_sample.prepare_jpyc_fee_samples(
        states_csv=Path("./data/polygon_fee_states_2025/polygon_daily_fee_states.csv"),
        inventory_csv=Path("./data/jpyc_inventory_2025_08_now_v2/jpyc_transfer_events_rpc.csv"),
        out_dir=Path("./data/jpyc_fee_state_sample"),
        anchor_block=74_647_463,
        sample_size_per_group=300,
        random_seed=20260924,
    )
    result["summary"]

標本群の定義:
  - low_baseline: 新JPYC開始後の、基準ガス価格が低位の日
  - spike_day: 日次平均ガス価格が14日基準の2倍以上かつ+100 gwei以上の日
  - post_expansion_high_baseline: block gas limit 160M・block time 1.5秒の供給枠に
    入った後、基準ガス価格が高位の日

日付の割当は、2025-08-01 00:00 UTCの既知ブロックを起点に日次block_countを
累積して復元する。receipt取得時に各ブロックのtimestampを再取得して最終確認する。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED_STATE_COLUMNS = {
    "date",
    "block_count",
    "fee_state",
    "gas_limit_level_millions",
    "block_time_level_sec",
    "gas_capacity_per_sec",
}
REQUIRED_EVENT_COLUMNS = {
    "block_number",
    "transaction_hash",
    "event_kind",
    "from_address",
    "to_address",
}

GROUP_ORDER = ["low_baseline", "spike_day", "post_expansion_high_baseline"]
GROUP_LABELS = {
    "low_baseline": "低い基準ガス価格",
    "spike_day": "急騰日",
    "post_expansion_high_baseline": "供給枠拡大後の高い基準ガス価格",
}


def _build_calendar(states: pd.DataFrame, anchor_block: int, start_date: pd.Timestamp) -> pd.DataFrame:
    """日次block_countから、開始日以後の日別ブロック範囲を復元する。"""
    calendar = states.copy()
    calendar["date"] = pd.to_datetime(calendar["date"], utc=True, errors="raise").dt.normalize()
    calendar = calendar.loc[calendar["date"] >= start_date].sort_values("date").reset_index(drop=True)
    if calendar.empty:
        raise ValueError(f"{start_date.date()}以後の日次状態がありません。")
    if calendar["date"].duplicated().any():
        raise ValueError("states_csvの日付が重複しています。")
    calendar["block_count"] = pd.to_numeric(calendar["block_count"], errors="raise").astype("int64")
    if (calendar["block_count"] <= 0).any():
        raise ValueError("block_countに0以下の値があります。")
    calendar["start_block"] = anchor_block + calendar["block_count"].cumsum().shift(fill_value=0).astype("int64")
    calendar["end_block"] = calendar["start_block"] + calendar["block_count"] - 1
    return calendar


def _assign_calendar(events: pd.DataFrame, calendar: pd.DataFrame) -> pd.DataFrame:
    """イベントを推定された日別ブロック範囲へ割り当てる。"""
    assigned = events.copy()
    assigned["block_number"] = pd.to_numeric(assigned["block_number"], errors="raise").astype("int64")
    ends = calendar["end_block"].to_numpy(dtype="int64")
    starts = calendar["start_block"].to_numpy(dtype="int64")
    positions = np.searchsorted(ends, assigned["block_number"].to_numpy(dtype="int64"), side="left")
    valid = positions < len(calendar)
    valid_indices = np.flatnonzero(valid)
    valid[valid_indices] &= assigned.iloc[valid_indices]["block_number"].to_numpy(dtype="int64") >= starts[positions[valid_indices]]

    state_columns = [
        "date",
        "fee_state",
        "gas_limit_level_millions",
        "block_time_level_sec",
        "gas_capacity_per_sec",
    ]
    for column in state_columns:
        assigned[column] = pd.NA
    assigned.loc[valid, state_columns] = calendar.iloc[positions[valid]][state_columns].to_numpy()
    assigned = assigned.rename(columns={"date": "estimated_utc_date"})
    return assigned


def _transaction_candidates(assigned: pd.DataFrame) -> pd.DataFrame:
    """単一の通常Transferだけをreceipt比較の候補にする。"""
    work = assigned.copy()
    work["is_normal_transfer_event"] = (work["event_kind"] == "transfer").astype("int64")
    work["is_mint_or_burn_event"] = work["event_kind"].isin(["mint", "burn"]).astype("int64")
    candidates = (
        work.groupby("transaction_hash", as_index=False)
        .agg(
            block_number=("block_number", "min"),
            estimated_utc_date=("estimated_utc_date", "first"),
            fee_state=("fee_state", "first"),
            gas_limit_level_millions=("gas_limit_level_millions", "first"),
            block_time_level_sec=("block_time_level_sec", "first"),
            gas_capacity_per_sec=("gas_capacity_per_sec", "first"),
            transfer_event_count=("transaction_hash", "size"),
            normal_transfer_event_count=("is_normal_transfer_event", "sum"),
            mint_or_burn_event_count=("is_mint_or_burn_event", "sum"),
            from_address=("from_address", "first"),
            to_address=("to_address", "first"),
        )
    )
    candidates["is_single_normal_transfer"] = (
        (candidates["transfer_event_count"] == 1)
        & (candidates["normal_transfer_event_count"] == 1)
        & (candidates["mint_or_burn_event_count"] == 0)
        & candidates["estimated_utc_date"].notna()
    )
    candidates["sample_group"] = pd.NA
    normal = candidates["is_single_normal_transfer"]
    candidates.loc[normal & candidates["fee_state"].eq("low_baseline"), "sample_group"] = "low_baseline"
    candidates.loc[normal & candidates["fee_state"].eq("spike_day"), "sample_group"] = "spike_day"
    expanded_high = (
        normal
        & candidates["fee_state"].eq("high_baseline")
        & pd.to_numeric(candidates["gas_limit_level_millions"], errors="coerce").ge(160)
        & pd.to_numeric(candidates["block_time_level_sec"], errors="coerce").le(1.5)
    )
    candidates.loc[expanded_high, "sample_group"] = "post_expansion_high_baseline"
    return candidates


def _sample(candidates: pd.DataFrame, size: int, seed: int) -> pd.DataFrame:
    selected: list[pd.DataFrame] = []
    for offset, group in enumerate(GROUP_ORDER):
        pool = candidates.loc[candidates["sample_group"].eq(group)].copy()
        if pool.empty:
            raise ValueError(f"標本候補がありません: {group}")
        n = min(size, len(pool))
        chosen = pool.sample(n=n, replace=False, random_state=seed + offset).copy()
        chosen["sample_group_label"] = GROUP_LABELS[group]
        chosen["sampling_seed"] = seed + offset
        selected.append(chosen)
    return pd.concat(selected, ignore_index=True).sort_values(["sample_group", "estimated_utc_date", "transaction_hash"]).reset_index(drop=True)


def prepare_jpyc_fee_samples(
    states_csv: Path,
    inventory_csv: Path,
    out_dir: Path,
    anchor_block: int = 74_647_463,
    sample_size_per_group: int = 300,
    random_seed: int = 20_260_924,
) -> dict[str, object]:
    """候補表と固定済みのreceipt取得用標本を保存する。"""
    states = pd.read_csv(states_csv)
    events = pd.read_csv(inventory_csv)
    missing_states = REQUIRED_STATE_COLUMNS - set(states.columns)
    missing_events = REQUIRED_EVENT_COLUMNS - set(events.columns)
    if missing_states:
        raise ValueError(f"states_csvに必要な列がありません: {sorted(missing_states)}")
    if missing_events:
        raise ValueError(f"inventory_csvに必要な列がありません: {sorted(missing_events)}")
    if sample_size_per_group <= 0:
        raise ValueError("sample_size_per_groupは1以上にしてください。")

    start_date = pd.Timestamp("2025-08-01", tz="UTC")
    calendar = _build_calendar(states, anchor_block=anchor_block, start_date=start_date)
    assigned = _assign_calendar(events, calendar)
    candidates = _transaction_candidates(assigned)
    selected = _sample(candidates, size=sample_size_per_group, seed=random_seed)

    summary = (
        candidates.assign(sample_group=candidates["sample_group"].fillna("not_sampled"))
        .groupby("sample_group", as_index=False)
        .agg(
            unique_transactions=("transaction_hash", "size"),
            single_normal_transfer_transactions=("is_single_normal_transfer", "sum"),
            first_estimated_date=("estimated_utc_date", "min"),
            last_estimated_date=("estimated_utc_date", "max"),
        )
    )
    selected_summary = (
        selected.groupby(["sample_group", "sample_group_label"], as_index=False)
        .agg(
            selected_transactions=("transaction_hash", "size"),
            first_estimated_date=("estimated_utc_date", "min"),
            last_estimated_date=("estimated_utc_date", "max"),
        )
    )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    calendar_path = out_dir / "estimated_daily_block_calendar.csv"
    candidates_path = out_dir / "jpyc_transaction_candidates.csv"
    selected_path = out_dir / "selected_transactions.csv"
    summary_path = out_dir / "sampling_summary.csv"
    manifest_path = out_dir / "sampling_manifest.json"
    calendar.to_csv(calendar_path, index=False)
    candidates.to_csv(candidates_path, index=False)
    selected.to_csv(selected_path, index=False)
    selected_summary.to_csv(summary_path, index=False)
    manifest = {
        "scope": "新JPYC（0xE7C3D8C9a439feDe00D2600032D5dB0Be71C3c29）のみ。旧JPYCは対象外。",
        "anchor_block": anchor_block,
        "anchor_time_utc": "2025-08-01T00:00:00Z",
        "sampling_unit": "transaction_hash。単一の通常Transferイベントだけを対象とする。",
        "sample_size_per_group_requested": sample_size_per_group,
        "random_seed": random_seed,
        "groups": {
            "low_baseline": "fee_state=low_baseline",
            "spike_day": "fee_state=spike_day",
            "post_expansion_high_baseline": "fee_state=high_baseline かつ block gas limit=160M、block time=1.5秒",
        },
        "date_assignment_caveat": "日次block_countの累積による推定日。receipt取得時にブロックtimestampを再取得し、最終的な日付・状態を再確認する。",
        "files": {
            "estimated_daily_block_calendar": str(calendar_path),
            "all_transaction_candidates": str(candidates_path),
            "selected_transactions_for_receipts": str(selected_path),
            "sampling_summary": str(summary_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["files"]["manifest"] = str(manifest_path)

    print(f"Calendar: {calendar['date'].min().date()} to {calendar['date'].max().date()}")
    print(f"Single normal transfer candidates: {int(candidates['is_single_normal_transfer'].sum()):,}")
    print(f"Selected receipts: {len(selected):,}")
    print(f"Saved: {selected_path}")
    return {"calendar": calendar, "candidates": candidates, "selected": selected, "summary": selected_summary, "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--states-csv", type=Path, required=True)
    parser.add_argument("--inventory-csv", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--anchor-block", type=int, default=74_647_463)
    parser.add_argument("--sample-size-per-group", type=int, default=300)
    parser.add_argument("--random-seed", type=int, default=20_260_924)
    args = parser.parse_args()
    prepare_jpyc_fee_samples(**vars(args))


if __name__ == "__main__" and "ipykernel" not in sys.modules:
    main()
