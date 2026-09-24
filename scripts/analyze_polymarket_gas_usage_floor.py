"""PolymarketがPolygonの平常時gasUsedをどれだけ構成したかを分解する。

日次のPolygon全体gasUsed/秒に、各日20ブロックの無作為標本で得た
確認済みPolymarket直接宛先5アドレスのgasUsed比を掛ける。
ここで得るのは「当該5宛先に直接届いた取引が観測上占めたgasUsed」の推定値であり、
内部callを含まない保守的な下限である。
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


POLYMARKET_ADDRESSES = {
    "0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0",
    "0x78769d50be1763ed1ca0d5e878d93f05aabff29e",
    "0xb768891e3130f6df18214ac804d4db76c2c37730",
    "0xe111180000d2663c0091e4f400237545b87b996b",
    "0xe2222d279d744050d28e00520010520000310f59",
}


def _daily_sample_share(detail: pd.DataFrame) -> pd.DataFrame:
    detail = detail.copy()
    detail["target_utc_date"] = pd.to_datetime(detail["target_utc_date"], utc=True).dt.normalize()
    detail["recipient_address"] = detail["recipient_address"].str.lower()
    all_gas = detail.groupby("target_utc_date", as_index=False).agg(sampled_total_gas_used=("gas_used", "sum"))
    pm_gas = (
        detail.loc[detail["recipient_address"].isin(POLYMARKET_ADDRESSES)]
        .groupby("target_utc_date", as_index=False)
        .agg(sampled_polymarket_direct_gas_used=("gas_used", "sum"))
    )
    out = all_gas.merge(pm_gas, on="target_utc_date", how="left").fillna({"sampled_polymarket_direct_gas_used": 0})
    out["polymarket_direct_gas_share"] = out["sampled_polymarket_direct_gas_used"] / out["sampled_total_gas_used"]
    return out


def _period_summary(panel: pd.DataFrame) -> pd.DataFrame:
    metrics = [
        "gas_used_per_sec",
        "estimated_polymarket_direct_gas_used_per_sec",
        "estimated_non_polymarket_gas_used_per_sec",
        "polymarket_direct_gas_share",
        "utilization",
        "gas_capacity_per_sec",
    ]
    rows: list[dict[str, object]] = []
    for phase, g in panel.groupby("comparison_period", sort=False):
        for metric in metrics:
            rows.append(
                {
                    "comparison_period": phase,
                    "metric": metric,
                    "days": len(g),
                    "mean": g[metric].mean(),
                    "median": g[metric].median(),
                    "min": g[metric].min(),
                    "max": g[metric].max(),
                }
            )
    summary = pd.DataFrame(rows)
    pivot = summary.pivot(index="metric", columns="comparison_period", values="mean")
    if {"2025年の平常期", "2026年の平常期"}.issubset(pivot.columns):
        summary = summary.merge(
            (pivot["2026年の平常期"] - pivot["2025年の平常期"]).rename("mean_change_2026_minus_2025"),
            left_on="metric",
            right_index=True,
            how="left",
        )
    return summary


def analyze_gas_usage_floor(
    detail_csv: Path,
    states_csv: Path,
    out_dir: Path,
    early_end_utc: str = "2025-12-31",
    later_start_utc: str = "2026-01-20",
) -> dict[str, object]:
    """急騰日と移行期間を除いた、二つの平常期のgasUsed水準を比べる。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    early_end = pd.Timestamp(early_end_utc, tz="UTC")
    later_start = pd.Timestamp(later_start_utc, tz="UTC")
    if later_start <= early_end:
        raise ValueError("later_start_utcはearly_end_utcより後にしてください。")

    shares = _daily_sample_share(pd.read_csv(detail_csv))
    states = pd.read_csv(states_csv)
    states["date"] = pd.to_datetime(states["date"], utc=True).dt.normalize()
    panel = states.merge(shares, left_on="date", right_on="target_utc_date", how="inner", validate="one_to_one")
    panel["estimated_polymarket_direct_gas_used_per_sec"] = panel["gas_used_per_sec"] * panel["polymarket_direct_gas_share"]
    panel["estimated_non_polymarket_gas_used_per_sec"] = panel["gas_used_per_sec"] - panel["estimated_polymarket_direct_gas_used_per_sec"]
    panel["comparison_period"] = np.select(
        [
            (panel["date"] <= early_end) & (~panel["is_spike_day"]),
            (panel["date"] >= later_start) & (~panel["is_spike_day"]),
        ],
        ["2025年の平常期", "2026年の平常期"],
        default="比較対象外（急騰日または移行期）",
    )
    comparison = panel.loc[panel["comparison_period"].isin(["2025年の平常期", "2026年の平常期"])].copy()
    summary = _period_summary(comparison)

    plt.rcParams["font.family"] = "Hiragino Sans"
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 8), sharex=True, gridspec_kw={"height_ratios": [3, 2]})
    ax1.plot(panel["date"], panel["gas_used_per_sec"] / 1e6, color="#5a6b85", linewidth=1.1, label="Polygon全体")
    ax1.plot(panel["date"], panel["estimated_polymarket_direct_gas_used_per_sec"] / 1e6, color="#7e57c2", linewidth=1.6, label="推定Polymarket直接宛先分（5アドレス）")
    ax1.set_ylabel("gasUsed / 秒（Mgas）")
    ax1.grid(axis="y", alpha=0.25)
    ax1.legend(loc="upper left", frameon=False)
    ax1.set_title("Polygonの平常時gasUsedは、Polymarket直接宛先分でどこまで底上げされたか", loc="left", fontsize=17, fontweight="bold")

    ax2.stackplot(
        panel["date"],
        panel["estimated_non_polymarket_gas_used_per_sec"] / 1e6,
        panel["estimated_polymarket_direct_gas_used_per_sec"] / 1e6,
        colors=["#c8d0dc", "#7e57c2"],
        labels=["その他", "推定Polymarket直接宛先分"],
        alpha=0.88,
    )
    ax2.set_ylabel("gasUsed / 秒（Mgas）")
    ax2.grid(axis="y", alpha=0.25)
    ax2.legend(loc="upper left", frameon=False, ncol=2)
    for ax in (ax1, ax2):
        ax.axvspan(panel["date"].min(), early_end, color="#d9f0d3", alpha=0.18, zorder=0)
        ax.axvspan(later_start, panel["date"].max(), color="#ffe6c7", alpha=0.18, zorder=0)
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.text(
        0.01,
        0.01,
        "色帯は比較する平常期。急騰日とその間の移行期は集計から除外。PM分は日次20ブロック標本の直接宛先比を全体gasUsed/秒に掛けた推定で、内部callを含まない。",
        fontsize=9.5,
        color="#52627a",
    )
    fig.tight_layout(rect=(0, 0.045, 1, 1))
    figure_path = out_dir / "polygon_polymarket_gas_usage_floor.png"
    fig.savefig(figure_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    panel_path = out_dir / "daily_polymarket_gas_usage_floor_panel.csv"
    summary_path = out_dir / "polymarket_gas_usage_floor_summary.csv"
    panel.to_csv(panel_path, index=False)
    summary.to_csv(summary_path, index=False)
    manifest = {
        "question": "平常時のPolygon全体gasUsed/秒が、確認済みPolymarket直接宛先分で持続的に底上げされたか。",
        "comparison": {
            "early_period": f"開始日から{early_end.date()}まで（急騰日を除外）",
            "later_period": f"{later_start.date()}から最終日まで（急騰日を除外）",
            "excluded": "急騰日および二期間の間の移行期",
        },
        "estimation": "Polygon全体の日次gasUsed/秒 × 当日20無作為ブロックにおける確認済みPolymarket直接宛先5アドレスのgasUsed比。内部callは含まないため、PM活動の全量推定ではない。",
        "not_claimed": "この分解は観測上のgasUsed構成を示す。PMがガス価格をどの程度引き上げたかの因果効果は推定しない。",
        "files": {"daily_panel": str(panel_path), "summary": str(summary_path), "figure": str(figure_path)},
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["files"]["manifest"] = str(manifest_path)
    return {"panel": panel, "comparison": comparison, "summary": summary, "manifest": manifest}
