"""直接JPYC transferの実費を、ガス価格状態ごとに散布図で可視化する。"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


plt.rcParams["font.family"] = ["Hiragino Sans", "Arial Unicode MS", "DejaVu Sans"]

ORDER = ["low_baseline", "spike_day", "post_expansion_high_baseline"]
LABELS = {
    "low_baseline": "低い基準\nガス価格",
    "spike_day": "急騰日",
    "post_expansion_high_baseline": "供給枠拡大後の\n高い基準ガス価格",
}
COLORS = {
    "low_baseline": "#65a30d",
    "spike_day": "#dc2626",
    "post_expansion_high_baseline": "#ea580c",
}


def plot_distribution(screened_csv: Path, output_png: Path) -> Path:
    """局面別の実費分布と、ガス価格対実費の散布図を保存する。"""
    frame = pd.read_csv(screened_csv)
    required = {"sample_group", "is_direct_jpyc_transfer", "fee_jpy", "fee_pol", "effective_gas_price_gwei"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"必要な列がありません: {sorted(missing)}")
    frame = frame.loc[frame["is_direct_jpyc_transfer"].astype(bool)].copy()
    frame = frame.loc[frame["sample_group"].isin(ORDER)].copy()
    if frame.empty:
        raise ValueError("直接JPYC transferがありません。")
    frame["fee_jpy"] = pd.to_numeric(frame["fee_jpy"], errors="coerce")
    frame["fee_pol"] = pd.to_numeric(frame["fee_pol"], errors="coerce")
    frame["effective_gas_price_gwei"] = pd.to_numeric(frame["effective_gas_price_gwei"], errors="coerce")
    frame = frame.dropna(subset=["fee_jpy", "fee_pol", "effective_gas_price_gwei"])
    frame = frame.loc[(frame["fee_jpy"] > 0) & (frame["effective_gas_price_gwei"] > 0)]

    figure, (ax_distribution, ax_relationship) = plt.subplots(1, 2, figsize=(14, 6), gridspec_kw={"width_ratios": [1, 1.15]})
    figure.subplots_adjust(top=0.82, bottom=0.19, wspace=0.25)
    figure.suptitle("新JPYCの直接送金：ガス価格状態ごとの実支払手数料（円）", x=0.08, ha="left", fontsize=16, weight="bold")

    rng = np.random.default_rng(20_260_924)
    for index, group in enumerate(ORDER):
        part = frame.loc[frame["sample_group"].eq(group)]
        if part.empty:
            continue
        x = index + rng.uniform(-0.16, 0.16, len(part))
        ax_distribution.scatter(x, part["fee_jpy"], s=18, alpha=0.55, color=COLORS[group], edgecolors="none")
        median = part["fee_jpy"].median()
        mean = part["fee_jpy"].mean()
        maximum = part["fee_jpy"].max()
        ax_distribution.hlines(median, index - 0.23, index + 0.23, color="#0f172a", linewidth=2.0, zorder=3)
        ax_distribution.hlines(mean, index - 0.23, index + 0.23, color="#334155", linewidth=1.4, linestyle="--", zorder=3)
        ax_distribution.text(index, median, f"中央値 ¥{median:.2f}", ha="center", va="bottom", fontsize=8, color="#0f172a")
        ax_distribution.text(index, maximum, f"最大 ¥{maximum:.2f}", ha="center", va="bottom", fontsize=8, color="#475569")
    ax_distribution.set_yscale("log")
    ax_distribution.set_xticks(range(len(ORDER)), [LABELS[group] for group in ORDER])
    ax_distribution.set_ylabel("実支払手数料（円、対数目盛）")
    ax_distribution.grid(axis="y", alpha=0.25)
    ax_distribution.set_title("局面ごとの分布（実線＝中央値、破線＝平均）", fontsize=11, loc="left")

    for group in ORDER:
        part = frame.loc[frame["sample_group"].eq(group)]
        if part.empty:
            continue
        ax_relationship.scatter(
            part["effective_gas_price_gwei"],
            part["fee_jpy"],
            s=22,
            alpha=0.62,
            color=COLORS[group],
            edgecolors="none",
            label=f"{LABELS[group].replace(chr(10), '')}（n={len(part)}）",
        )
    ax_relationship.set_xscale("log")
    ax_relationship.set_yscale("log")
    ax_relationship.set_xlabel("実効ガス価格（gwei、対数目盛）")
    ax_relationship.set_ylabel("実支払手数料（円、対数目盛）")
    ax_relationship.grid(alpha=0.25)
    ax_relationship.set_title("ガス価格が高い局面ほど実費も上がる", fontsize=11, loc="left")
    ax_relationship.legend(frameon=False, fontsize=8, loc="upper left")

    figure.text(
        0.08,
        0.045,
        "対象＝新JPYCコントラクトを直接呼ぶ ERC-20 transfer のみ。実費＝gasUsed × effectiveGasPrice。円換算＝取引日のPOL/USD × USD/JPY（日次値）。",
        fontsize=8.5,
        color="#475569",
    )
    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_png, dpi=180, bbox_inches="tight")
    plt.close(figure)
    print(f"Saved: {output_png}")
    return output_png


if __name__ == "__main__" and "ipykernel" not in sys.modules:
    raise SystemExit("Jupyterではrun_plot_jpyc_direct_transfer_fee_distribution.pyを%runしてください。")
