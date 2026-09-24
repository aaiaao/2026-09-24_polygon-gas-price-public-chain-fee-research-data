"""全日層化標本から、確認済みPolymarket宛先群の日次gasUsed構成を描く。

これはtop-level宛先への集計であり、内部callを含むPolymarket全体のガス量ではない。
従って「確認済みの直接宛先群」と「その他」の構成変化を示す図として扱う。
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


GROUPS = {
    "通常市場：Fee Module": {"0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0"},
    "Neg Risk市場：Fee Module": {
        "0xb768891e3130f6df18214ac804d4db76c2c37730",
        "0x78769d50be1763ed1ca0d5e878d93f05aabff29e",
    },
    "通常市場：CTF Exchange V2": {"0xe111180000d2663c0091e4f400237545b87b996b"},
    "Neg Risk市場：CTF Exchange V2": {"0xe2222d279d744050d28e00520010520000310f59"},
}
ORDER = [
    "通常市場：Fee Module",
    "Neg Risk市場：Fee Module",
    "通常市場：CTF Exchange V2",
    "Neg Risk市場：CTF Exchange V2",
    "その他のトップレベル宛先",
]
COLORS = {
    "通常市場：Fee Module": "#7e57c2",
    "Neg Risk市場：Fee Module": "#ef5350",
    "通常市場：CTF Exchange V2": "#1e88e5",
    "Neg Risk市場：CTF Exchange V2": "#26a69a",
    "その他のトップレベル宛先": "#cfd6df",
}

# Polymarket公式のExchange Upgrade告知にある、V1→V2のカットオーバー時刻。
# 日次標本ではこの日が旧Fee Module群とV2群の混在日になる。
V1_TO_V2_CUTOVER_UTC = pd.Timestamp("2026-04-28T11:00:00Z")


def _rpc_call(rpc_url: str, method: str, params: list[object]) -> object:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    request = Request(rpc_url, data=body, headers={"Content-Type": "application/json", "User-Agent": "polygon-gas-research/1.0"})
    with urlopen(request, timeout=90) as response:  # noqa: S310 - user supplies public RPC URL.
        answer = json.load(response)
    if "error" in answer:
        raise RuntimeError(str(answer["error"]))
    return answer["result"]


def fetch_creation_dates(rpc_url: str, out_dir: Path) -> pd.DataFrame:
    """履歴eth_getCodeの二分探索で、5コントラクトの初めてcodeが現れるブロックを得る。"""
    cache_path = out_dir / "known_recipient_contract_creation_dates.csv"
    addresses = sorted({address for values in GROUPS.values() for address in values})
    if cache_path.exists():
        cached = pd.read_csv(cache_path)
        if set(cached["address"].str.lower()) == set(addresses):
            return cached
    latest = int(_rpc_call(rpc_url, "eth_blockNumber", []), 16)
    rows = []
    labels = {address: label for label, values in GROUPS.items() for address in values}
    for index, address in enumerate(addresses, start=1):
        low, high = 0, latest
        while low < high:
            middle = (low + high) // 2
            code = str(_rpc_call(rpc_url, "eth_getCode", [address, hex(middle)]))
            if code in {"0x", "0x0"}:
                low = middle + 1
            else:
                high = middle
        block = _rpc_call(rpc_url, "eth_getBlockByNumber", [hex(low), False])
        created_at = pd.to_datetime(int(block["timestamp"], 16), unit="s", utc=True)
        rows.append({"address": address, "recipient_group": labels[address], "creation_block": low, "creation_timestamp_utc": created_at.isoformat(), "creation_utc_date": created_at.normalize().date().isoformat(), "method": "historical eth_getCode binary search"})
        print(f"作成日確認: {index}/{len(addresses)} {labels[address]} = {created_at.date()}", flush=True)
    output = pd.DataFrame(rows)
    output.to_csv(cache_path, index=False)
    return output


def _address_group(address: str) -> str:
    address = address.lower()
    for label, addresses in GROUPS.items():
        if address in addresses:
            return label
    return "その他のトップレベル宛先"


def build_daily_known_recipient_groups(input_dir: Path, out_dir: Path, creation_dates: pd.DataFrame | None = None) -> dict[str, object]:
    input_dir, out_dir = Path(input_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    detail = pd.read_csv(input_dir / "recipient_gas_by_sampled_block.csv")
    detail["target_utc_date"] = pd.to_datetime(detail["target_utc_date"], utc=True).dt.normalize()
    detail["recipient_group"] = detail["recipient_address"].map(_address_group)
    detail["is_known_polymarket_direct_recipient"] = detail["recipient_group"].ne("その他のトップレベル宛先")
    block_totals = detail[["phase", "sampled_block_number", "block_gas_used"]].drop_duplicates(["phase", "sampled_block_number"])
    selected = pd.read_csv(input_dir / "selected_blocks.csv")
    selected_counts = selected.groupby("phase", as_index=False).agg(sampled_blocks=("sampled_block_number", "nunique"))
    phase_summary = (
        block_totals.groupby("phase", as_index=False)
        .agg(sampled_blocks_with_transactions=("sampled_block_number", "nunique"), total_gas_used=("block_gas_used", "sum"))
        .merge(selected_counts, on="phase", how="left", validate="one_to_one")
        .merge(
            detail.groupby("phase", as_index=False).agg(all_unique_top_level_recipients=("recipient_address", "nunique"), all_transaction_count=("transaction_count", "sum")),
            on="phase", how="left",
        )
    )
    other_summary = (
        detail.loc[~detail["is_known_polymarket_direct_recipient"]]
        .groupby("phase", as_index=False)
        .agg(other_unique_top_level_recipients=("recipient_address", "nunique"), other_transaction_count=("transaction_count", "sum"), other_gas_used=("gas_used", "sum"))
    )
    phase_summary = phase_summary.merge(other_summary, on="phase", how="left").fillna(0)
    phase_summary["other_transaction_share"] = phase_summary["other_transaction_count"] / phase_summary["all_transaction_count"]
    phase_summary["other_gas_share"] = phase_summary["other_gas_used"] / phase_summary["total_gas_used"]
    grouped = (
        detail.groupby(["phase", "target_utc_date", "recipient_group"], as_index=False)
        .agg(gas_used=("gas_used", "sum"), transaction_count=("transaction_count", "sum"), sampled_blocks=("sampled_block_number", "nunique"))
    )
    totals = (
        detail[["phase", "target_utc_date", "sampled_block_number", "block_gas_used"]]
        .drop_duplicates(["phase", "target_utc_date", "sampled_block_number"])
        .groupby(["phase", "target_utc_date"], as_index=False)
        .agg(sampled_blocks=("sampled_block_number", "nunique"), total_gas_used=("block_gas_used", "sum"))
    )
    daily = grouped.merge(totals, on=["phase", "target_utc_date"], how="left", validate="many_to_one")
    daily["gas_share"] = daily["gas_used"] / daily["total_gas_used"]
    # 空ブロックだけの日でも、全構成比が0にならないようOtherを明示的に補う。
    present = set(zip(daily["phase"], daily["target_utc_date"]))
    for row in totals.itertuples(index=False):
        key = (row.phase, row.target_utc_date)
        if key not in present:
            daily = pd.concat([daily, pd.DataFrame([{
                "phase": row.phase, "target_utc_date": row.target_utc_date,
                "recipient_group": "その他のトップレベル宛先", "gas_used": 0,
                "transaction_count": 0, "sampled_blocks_x": 0,
                "sampled_blocks_y": row.sampled_blocks, "total_gas_used": row.total_gas_used,
                "gas_share": 0.0,
            }])], ignore_index=True)
    wide = daily.pivot_table(index=["phase", "target_utc_date", "sampled_blocks_y", "total_gas_used"], columns="recipient_group", values="gas_share", aggfunc="sum", fill_value=0).reset_index()
    wide = wide.rename(columns={"sampled_blocks_y": "sampled_blocks"})
    for name in ORDER:
        if name not in wide:
            wide[name] = 0.0
    wide["その他のトップレベル宛先"] = 1 - wide[[name for name in ORDER if name != "その他のトップレベル宛先"]].sum(axis=1)
    wide = wide.sort_values("target_utc_date").reset_index(drop=True)

    plt.rcParams["font.family"] = "Hiragino Sans"
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    # この表には3局面の日だけが入る。対象外の長い空白を直線で結ぶと、存在しない
    # 日次推移に見えるため、7日超の欠損では描画を明示的に切る。
    wide_plot = wide.copy()
    gaps = wide_plot["target_utc_date"].diff().dt.days.gt(7)
    wide_plot.loc[gaps, ORDER + ["total_gas_used", "sampled_blocks"]] = float("nan")
    x = wide_plot["target_utc_date"]
    lower = pd.Series(0.0, index=wide.index)
    for name in ORDER:
        upper = lower + wide_plot[name] * 100
        ax1.fill_between(x, lower, upper, color=COLORS[name], label=name, linewidth=0)
        lower = upper
    ax1.set_ylim(0, 100)
    ax1.set_ylabel("標本内gasUsed構成比（%）")
    ax1.set_title("Polygon：確認済みPolymarket直接宛先群の日次gasUsed構成（全対象日×20ブロック標本）", loc="left", fontsize=17, fontweight="bold")
    ax1.grid(axis="y", alpha=0.25)
    if x.min() <= V1_TO_V2_CUTOVER_UTC <= x.max():
        ax1.axvline(V1_TO_V2_CUTOVER_UTC, color="#263238", linewidth=1.4, linestyle="--", zorder=5)
        ax1.annotate(
            "2026-04-28 11:00 UTC\nV1→V2移行",
            xy=(V1_TO_V2_CUTOVER_UTC, 98),
            xytext=(5, -3),
            textcoords="offset points",
            ha="left",
            va="top",
            fontsize=8.5,
            color="#263238",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.78, "pad": 1.5},
        )
    ax1.legend(loc="upper left", ncol=1, frameon=False)
    if creation_dates is not None and not creation_dates.empty:
        creation_dates = creation_dates.copy()
        creation_dates["creation_utc_date"] = pd.to_datetime(creation_dates["creation_utc_date"], utc=True)
        visible = creation_dates.loc[creation_dates["creation_utc_date"].between(x.min(), x.max())]
        for index, row in enumerate(visible.itertuples(index=False)):
            line_x = row.creation_utc_date
            ax1.axvline(line_x, color="#263238", linewidth=1, linestyle="--", alpha=0.75)
            # 同日作成の線が重なる場合も、名称は縦にずらして読めるようにする。
            ax1.annotate(f"作成\n{row.recipient_group}", xy=(line_x, 99), xytext=(3, -3 - index % 3 * 17), textcoords="offset points", ha="left", va="top", fontsize=7.5, color="#263238")
    ax2.plot(x, wide_plot["total_gas_used"] / wide_plot["sampled_blocks"] / 1_000_000, color="#455a73", linewidth=1.6)
    ax2.set_ylabel("標本1ブロック\n平均gasUsed（Mgas）")
    ax2.grid(axis="y", alpha=0.25)
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.text(0.01, 0.01, "対象は確認済み5アドレスのトップレベル宛先のみ。内部callや未ラベル宛先は「その他」。構成変化の記述用であり、ガス価格への因果を示すものではない。", fontsize=9.5, color="#52627a")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    figure_path = out_dir / "polygon_daily_known_polymarket_recipient_share.png"
    fig.savefig(figure_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    daily_path = out_dir / "daily_known_recipient_group_gas_share.csv"
    wide_path = out_dir / "daily_known_recipient_group_gas_share_wide.csv"
    phase_summary_path = out_dir / "phase_other_recipient_scope_summary.csv"
    daily.to_csv(daily_path, index=False)
    wide.to_csv(wide_path, index=False)
    phase_summary.to_csv(phase_summary_path, index=False)
    manifest = {
        "scope": "全対象日の各日20ブロック標本。確認済みPolymarket直接宛先5アドレスとその他のtop-level宛先を比較。",
        "not_measured": "内部call先への帰属、未ラベル宛先に含まれるPolymarket関連、因果関係。",
        "groups": {name: sorted(addresses) for name, addresses in GROUPS.items()},
        "files": {"long_table": str(daily_path), "wide_table": str(wide_path), "phase_scope_summary": str(phase_summary_path), "figure": str(figure_path)},
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["files"]["manifest"] = str(manifest_path)
    return {"daily": daily, "wide": wide, "phase_summary": phase_summary, "manifest": manifest}
