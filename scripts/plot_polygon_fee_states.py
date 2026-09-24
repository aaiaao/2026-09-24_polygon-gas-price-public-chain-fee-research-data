"""polygon_daily_fee_states.csvから、ガス価格の状態を背景帯で示すPNGを作る。

Notebookセルでの実行例:

    from pathlib import Path
    from plot_polygon_fee_states import plot_fee_states

    plot_fee_states(
        states_csv=Path("./data/polygon_fee_states_2025/polygon_daily_fee_states.csv"),
        output_png=Path("./data/polygon_fee_states_2025/polygon_fee_states_timeline.png"),
    )
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch


plt.rcParams["font.family"] = ["Hiragino Sans", "Arial Unicode MS", "DejaVu Sans"]


STATE_COLORS = {
    "insufficient_history": "#cbd5e1",
    "low_baseline": "#d9ef8b",
    "middle_baseline": "#fee08b",
    "high_baseline": "#fdae61",
    "spike_day": "#d73027",
}
STATE_LABELS = {
    "insufficient_history": "基準計算前",
    "low_baseline": "基準ガス価格：低位",
    "middle_baseline": "基準ガス価格：中位",
    "high_baseline": "基準ガス価格：高位",
    "spike_day": "急騰日",
}

# 日次CSVで新しい供給水準が初めて観測された日。時刻単位の実装時刻ではない。
CAPACITY_MILESTONES = [
    ("2026-03-13", "110M / 2秒"),
    ("2026-03-24", "120M / 2秒"),
    ("2026-05-09", "140M / 1.75秒"),
    ("2026-06-04", "140M / 1.5秒"),
    ("2026-06-18", "160M / 1.5秒"),
]

# 本文・JPYC標本・後段の需要分析で共通して使う三つの比較期。
# 背景帯は日々の価格状態を既に示しているため、ここでは重ね塗りせず横点線で示す。
COMPARISON_PHASES = [
    ("2025-01-01", "2025-12-31", "① 2025年：低価格期"),
    ("2026-01-01", "2026-02-28", "② 2026年1〜2月：急騰日"),
    ("2026-06-18", None, "③ 処理可能量拡大後：直近"),
]


def _runs(frame: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.Timestamp, str]]:
    """同一ガス価格状態が連続する区間を、背景帯描画用にまとめる。"""
    state_changed = frame["fee_state"].ne(frame["fee_state"].shift())
    run_id = state_changed.cumsum()
    result: list[tuple[pd.Timestamp, pd.Timestamp, str]] = []
    for _, group in frame.groupby(run_id, sort=False):
        result.append((group["date"].iloc[0], group["date"].iloc[-1] + pd.Timedelta(days=1), group["fee_state"].iloc[0]))
    return result


def plot_fee_states(states_csv: Path, output_png: Path) -> Path:
    """日次ガス価格、価格状態の背景帯、最大gas処理枠を二段チャートとして保存する。"""
    frame = pd.read_csv(states_csv)
    required = {"date", "avg_gas_gwei", "fee_state", "gas_capacity_per_sec"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"必要な列がありません: {sorted(missing)}")
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame = frame.sort_values("date").reset_index(drop=True)

    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    figure, (ax_price, ax_capacity) = plt.subplots(
        2,
        1,
        figsize=(15, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.08},
    )
    figure.subplots_adjust(top=0.89)
    figure.suptitle("Polygon：日次平均ガス価格と基準水準・急騰日（2025年以降）", x=0.125, ha="left", fontsize=15, weight="bold")

    shown_states: set[str] = set()
    for start, end, state in _runs(frame):
        if state == "insufficient_history":
            continue
        color = STATE_COLORS.get(state, "#94a3b8")
        alpha = 0.20 if state != "spike_day" else 0.34
        ax_price.axvspan(start, end, color=color, alpha=alpha, linewidth=0)
        shown_states.add(state)

    ax_price.plot(frame["date"], frame["avg_gas_gwei"], color="#0284c7", linewidth=1.15)
    spikes = frame.loc[frame["fee_state"] == "spike_day"]
    ax_price.scatter(spikes["date"], spikes["avg_gas_gwei"], color=STATE_COLORS["spike_day"], s=16, zorder=3)
    ax_price.set_ylabel("Gas price (gwei)")
    ax_price.set_ylim(bottom=0)
    ax_price.grid(axis="y", alpha=0.25)

    # 比較期は背景帯と区別して、プロット上端の横点線で表示する。
    plot_start = frame["date"].min()
    plot_end = frame["date"].max()
    for index, (start_text, end_text, label) in enumerate(COMPARISON_PHASES):
        start = max(pd.Timestamp(start_text, tz="UTC"), plot_start)
        end = min(pd.Timestamp(end_text, tz="UTC") if end_text else plot_end, plot_end)
        if start > end:
            continue
        y = 0.965 - index * 0.055
        ax_price.plot(
            [start, end],
            [y, y],
            transform=ax_price.get_xaxis_transform(),
            color="#0f172a",
            linestyle=(0, (2, 2)),
            linewidth=1.7,
            solid_capstyle="butt",
            clip_on=False,
            zorder=4,
        )
        ax_price.text(
            start,
            y + 0.009,
            label,
            transform=ax_price.get_xaxis_transform(),
            fontsize=7.6,
            color="#0f172a",
            va="bottom",
            ha="left",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.78, "pad": 0.45},
            zorder=5,
        )
    state_order = ["low_baseline", "middle_baseline", "high_baseline", "spike_day"]
    legend_handles = [
        Patch(facecolor=STATE_COLORS[state], alpha=0.34 if state == "spike_day" else 0.20, label=STATE_LABELS[state])
        for state in state_order
        if state in shown_states
    ]
    legend_handles.append(Line2D([0], [0], color="#0284c7", linewidth=1.15, label="日次平均gas price"))
    figure.legend(
        handles=legend_handles,
        loc="upper left",
        bbox_to_anchor=(0.125, 0.94),
        ncol=5,
        frameon=False,
        fontsize=9,
    )

    ax_capacity.plot(frame["date"], frame["gas_capacity_per_sec"] / 1_000_000, color="#475569", linewidth=1.25)
    ax_capacity.set_ylabel("1秒あたりの最大gas枠\n(Mgas/秒)")
    ax_capacity.grid(axis="y", alpha=0.25)
    ax_capacity.set_ylim(bottom=0)
    ax_capacity.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax_capacity.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

    # 上段と下段で同じ供給変更日を示す。注記は下段に集約して価格線を隠さない。
    for index, (date_text, label) in enumerate(CAPACITY_MILESTONES):
        date = pd.Timestamp(date_text, tz="UTC")
        if not (frame["date"].min() <= date <= frame["date"].max()):
            continue
        for axis in (ax_price, ax_capacity):
            axis.axvline(date, color="#334155", linestyle="--", linewidth=0.8, alpha=0.55, zorder=2)
        y = 0.10 if index % 2 == 0 else 0.47
        ax_capacity.annotate(
            label,
            xy=(date, y),
            xycoords=("data", "axes fraction"),
            xytext=(3, 0),
            textcoords="offset points",
            fontsize=7.4,
            color="#334155",
            rotation=90,
            va="bottom",
            ha="left",
        )

    figure.text(
        0.01,
        0.01,
        "背景帯＝過去14日中央値に基づく暫定的な基準ガス価格の区分。赤帯・赤点＝日次平均が基準の2倍以上かつ+100 gwei以上。上段の横点線①〜③＝本文で比較する観測期。破線＝日次CSVで新しい処理能力の水準が初めて観測された日（実装時刻ではない）。下段＝1ブロックのgas上限 ÷ 実測ブロック生成間隔。",
        fontsize=8.5,
        color="#475569",
    )
    figure.savefig(output_png, dpi=180, bbox_inches="tight")
    plt.close(figure)
    print(f"Saved: {output_png}")
    return output_png


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--states-csv", type=Path, required=True)
    parser.add_argument("--output-png", type=Path, required=True)
    args = parser.parse_args()
    plot_fee_states(args.states_csv, args.output_png)


if __name__ == "__main__" and "ipykernel" not in sys.modules:
    main()
