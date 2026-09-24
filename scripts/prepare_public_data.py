"""Create privacy-minimized public tables from the private research outputs.

This script intentionally excludes individual JPYC transaction rows, raw logs,
unlabelled recipient addresses, and any RPC credentials.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def sanitise_phase_top_recipients(source: Path, destination: Path) -> None:
    table = pd.read_csv(source)
    known = table["label_status"].eq("confirmed")
    public = table.assign(
        recipient=table["recipient_label"].where(known, "未確認の宛先（アドレス非公開）"),
        verification=table["label_status"].map({"confirmed": "確認済み"}).fillna("未確認"),
    )[[
        "phase",
        "phase_label",
        "rank_in_phase",
        "recipient",
        "verification",
        "gas_used",
        "gas_share",
        "transaction_count",
        "sampled_blocks_seen",
        "label_source_url",
    ]]
    public.to_csv(destination, index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-data-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    root = args.private_data_root
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    sanitise_phase_top_recipients(
        root / "polygon_phase_recipient_gas_stratified_20_per_day_analysis" / "recipient_composition_top8_by_phase.csv",
        out / "phase_top_recipients_public.csv",
    )

    copies = {
        root / "polygon_fee_states_2025" / "polygon_daily_fee_states.csv": out / "polygon_daily_fee_states.csv",
        root / "polygon_daily_known_recipient_groups_all_days" / "daily_known_recipient_group_gas_share.csv": out / "polymarket_direct_group_daily_share.csv",
        root / "polygon_polymarket_gas_usage_floor" / "daily_polymarket_gas_usage_floor_panel.csv": out / "polymarket_gas_usage_floor_panel.csv",
        root / "polygon_polymarket_gas_usage_floor" / "polymarket_gas_usage_floor_summary.csv": out / "polymarket_gas_usage_floor_summary.csv",
        root / "jpyc_direct_transfer_fee_jpy" / "jpyc_direct_transfer_fee_summary_jpy.csv": out / "jpyc_direct_transfer_fee_summary_jpy.csv",
        root / "jpyc_direct_transfer_fee_jpy" / "usd_jpy_daily_rates.csv": out / "usd_jpy_daily_rates.csv",
        root / "polygon_all_days_recipient_gas_20_per_day" / "selected_blocks.csv": out / "sampled_blocks_all_days.csv",
    }
    for source, destination in copies.items():
        destination.write_bytes(source.read_bytes())


if __name__ == "__main__":
    main()
