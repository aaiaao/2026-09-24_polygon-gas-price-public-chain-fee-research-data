"""Polygonの料金局面ごとのトップレベル宛先別ガス需要を比較・可視化する。

入力は ``polygon_phase_recipient_gas_sample.py`` が作る標本集計。ここでの宛先は
取引の top-level ``to`` であり、内部 call まで追った実行コントラクトではない。
従って出力は「原因の確定」ではなく、次にラベル確認・同一プロトコル集約を行う
べき候補を絞るための地図である。
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PHASE_ORDER = ["low_baseline", "spike_day", "post_expansion_high_baseline"]
PHASE_LABELS = {
    "low_baseline": "低い基準\nガス価格",
    "spike_day": "急騰日",
    "post_expansion_high_baseline": "供給枠拡大後の\n高い基準ガス価格",
}
PHASE_COLORS = {"low_baseline": "#8dbf45", "spike_day": "#e95a5a", "post_expansion_high_baseline": "#ee8b48"}

# ラベルは、契約の検証済みソースまたは公式リポジトリで住所まで一致したものだけ。
# top-level宛先のため、ラベル＝内部実行先への完全な帰属、ではない。
KNOWN_RECIPIENTS = {
    "0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0": {
        "label": "Polymarket Fee Module", "status": "confirmed", "source": "https://polygonscan.com/address/0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0",
    },
    "0xb768891e3130f6df18214ac804d4db76c2c37730": {
        "label": "Polymarket Neg Risk Fee Module 2", "status": "confirmed", "source": "https://polygonscan.com/address/0xb768891e3130f6df18214ac804d4db76c2c37730",
    },
    "0x78769d50be1763ed1ca0d5e878d93f05aabff29e": {
        "label": "Polymarket Neg Risk Fee Module", "status": "confirmed", "source": "https://polygonscan.com/address/0x78769d50be1763ed1ca0d5e878d93f05aabff29e",
    },
    "0xe111180000d2663c0091e4f400237545b87b996b": {
        "label": "Polymarket CTF Exchange V2", "status": "confirmed", "source": "https://github.com/Polymarket/ctf-exchange-v2",
    },
    "0xe2222d279d744050d28e00520010520000310f59": {
        "label": "Polymarket Neg Risk CTF Exchange V2", "status": "confirmed", "source": "https://github.com/Polymarket/ctf-exchange-v2",
    },
}


def _short(address: str) -> str:
    if address == "contract_creation":
        return "コントラクト作成"
    if address in KNOWN_RECIPIENTS:
        return str(KNOWN_RECIPIENTS[address]["label"])
    return f"{address[:8]}…{address[-4:]}"


def _label_metadata(address: str) -> dict[str, str]:
    known = KNOWN_RECIPIENTS.get(address)
    return {
        "recipient_label": "" if known is None else str(known["label"]),
        "label_status": "unlabelled" if known is None else str(known["status"]),
        "label_source_url": "" if known is None else str(known["source"]),
    }


def _polygonscan(address: str) -> str:
    return "" if address == "contract_creation" else f"https://polygonscan.com/address/{address}"


def _phase_totals(detail: pd.DataFrame) -> pd.DataFrame:
    return (
        detail.groupby("phase", as_index=False)
        .agg(sampled_blocks=("sampled_block_number", "nunique"), sampled_gas_used=("gas_used", "sum"), sampled_transaction_count=("transaction_count", "sum"))
    )


def _composition(summary: pd.DataFrame, top_n: int) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for phase in PHASE_ORDER:
        group = summary.loc[summary["phase"].eq(phase)].sort_values("gas_used", ascending=False).copy()
        total = float(group["sampled_gas_used"].iloc[0])
        chosen = group.head(top_n).copy()
        for rank, row in enumerate(chosen.itertuples(index=False), start=1):
            rows.append({
                "phase": phase,
                "phase_label": PHASE_LABELS[phase],
                "rank_in_phase": rank,
                "recipient_address": row.recipient_address,
                "recipient_short": _short(row.recipient_address),
                "polygonscan_url": _polygonscan(row.recipient_address),
                "gas_used": int(row.gas_used),
                "gas_share": float(row.gas_used) / total,
                "transaction_count": int(row.transaction_count),
                "sampled_blocks_seen": int(row.sampled_blocks),
                **_label_metadata(row.recipient_address),
            })
        rows.append({
            "phase": phase,
            "phase_label": PHASE_LABELS[phase],
            "rank_in_phase": None,
            "recipient_address": "other_recipients",
            "recipient_short": "その他の宛先",
            "polygonscan_url": "",
            "gas_used": int(group.iloc[top_n:]["gas_used"].sum()),
            "gas_share": float(group.iloc[top_n:]["gas_used"].sum()) / total,
            "transaction_count": int(group.iloc[top_n:]["transaction_count"].sum()),
            "sampled_blocks_seen": None,
            **_label_metadata("other_recipients"),
        })
    return pd.DataFrame(rows)


def _cross_phase_table(summary: pd.DataFrame, top_per_phase: int) -> pd.DataFrame:
    ranked = summary.sort_values(["phase", "gas_used"], ascending=[True, False]).copy()
    ranked["rank_in_phase"] = ranked.groupby("phase").cumcount() + 1
    candidates = ranked.loc[ranked["rank_in_phase"].le(top_per_phase), "recipient_address"].unique()
    pivot = (
        summary.loc[summary["recipient_address"].isin(candidates)]
        .pivot(index="recipient_address", columns="phase", values="gas_share_of_phase_sample")
        .reindex(columns=PHASE_ORDER, fill_value=0.0)
        .fillna(0.0)
        .reset_index()
    )
    gas = (
        summary.loc[summary["recipient_address"].isin(candidates)]
        .pivot(index="recipient_address", columns="phase", values="gas_used")
        .reindex(columns=PHASE_ORDER, fill_value=0)
        .fillna(0)
        .reset_index()
    )
    tx = (
        summary.loc[summary["recipient_address"].isin(candidates)]
        .pivot(index="recipient_address", columns="phase", values="transaction_count")
        .reindex(columns=PHASE_ORDER, fill_value=0)
        .fillna(0)
        .reset_index()
    )
    out = pivot.merge(gas, on="recipient_address", suffixes=("_share", "_gas_used")).merge(
        tx, on="recipient_address", suffixes=("", "_tx_count")
    )
    # Rename after suffixing to keep the CSV self-explanatory.
    for phase in PHASE_ORDER:
        out = out.rename(columns={
            f"{phase}_share": f"{phase}_gas_share",
            f"{phase}_gas_used": f"{phase}_gas_used",
            phase: f"{phase}_transaction_count",
        })
    out["recipient_short"] = out["recipient_address"].map(_short)
    out["polygonscan_url"] = out["recipient_address"].map(_polygonscan)
    labels = out["recipient_address"].map(_label_metadata).apply(pd.Series)
    out = pd.concat([out, labels], axis=1)
    out["spike_minus_low_share_points"] = 100 * (out["spike_day_gas_share"] - out["low_baseline_gas_share"])
    out["post_minus_low_share_points"] = 100 * (out["post_expansion_high_baseline_gas_share"] - out["low_baseline_gas_share"])
    return out.sort_values(["spike_minus_low_share_points", "post_minus_low_share_points"], ascending=False).reset_index(drop=True)


def _plot(composition: pd.DataFrame, output_png: Path) -> None:
    # macOS 標準フォント。存在しないWindows/Linux向けフォントは指定しない。
    plt.rcParams["font.family"] = "Hiragino Sans"
    fig, axes = plt.subplots(1, 3, figsize=(15, 7), sharey=True)
    palette = plt.get_cmap("tab20")
    for ax, phase in zip(axes, PHASE_ORDER):
        group = composition.loc[composition["phase"].eq(phase)].copy()
        bottom = 0.0
        for index, row in group.iterrows():
            is_other = row["recipient_address"] == "other_recipients"
            color = "#d9dde3" if is_other else palette(int(row["rank_in_phase"] - 1) % 20)
            ax.bar(0, row["gas_share"] * 100, bottom=bottom * 100, width=0.72, color=color, edgecolor="white", linewidth=0.8)
            midpoint = (bottom + row["gas_share"] / 2) * 100
            # 小さい帯に住所を重ねると構成が読めなくなるため、4.5%以上だけ直接表示する。
            # 個別アドレスはCSV（Polygonscanリンク付き）で確認する。
            if row["gas_share"] >= 0.045:
                ax.text(0, midpoint, f"{row['recipient_short']}\n{row['gas_share']:.1%}", ha="center", va="center", fontsize=8.5)
            bottom += row["gas_share"]
        top1 = group.loc[group["recipient_address"].ne("other_recipients"), "gas_share"].max()
        ax.set_title(f"{PHASE_LABELS[phase]}\n上位宛先 1位: {top1:.1%}", fontsize=13, fontweight="bold")
        ax.set_xlim(-0.65, 0.65)
        ax.set_xticks([])
        ax.grid(axis="y", alpha=0.25)
        ax.set_axisbelow(True)
    axes[0].set_ylabel("標本内の gasUsed 構成比（%）", fontsize=12)
    axes[0].set_ylim(0, 100)
    fig.suptitle("Polygon：ガス価格局面ごとのトップレベル宛先別 gasUsed 構成（標本）", x=0.06, ha="left", y=0.98, fontsize=18, fontweight="bold")
    fig.text(0.06, 0.025, "各局面から無作為抽出したブロックの receipt gasUsed を、取引の top-level 宛先別に集計。内部 call の実行先ではないため、ここでは原因候補の地図として読む。", ha="left", fontsize=10, color="#52627a")
    fig.tight_layout(rect=(0.04, 0.07, 1, 0.92))
    fig.savefig(output_png, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def analyze_phase_recipient_gas(input_dir: Path, out_dir: Path, top_n_per_phase: int = 8) -> dict[str, object]:
    """標本結果を図表・候補台帳に変換する。"""
    input_dir, out_dir = Path(input_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    detail = pd.read_csv(input_dir / "recipient_gas_by_sampled_block.csv")
    summary = pd.read_csv(input_dir / "recipient_gas_summary_by_phase.csv")
    missing = set(PHASE_ORDER) - set(summary["phase"])
    if missing:
        raise ValueError(f"必要な局面がありません: {sorted(missing)}")
    composition = _composition(summary, top_n=top_n_per_phase)
    cross = _cross_phase_table(summary, top_per_phase=top_n_per_phase)
    totals = _phase_totals(detail).sort_values("phase")
    concentration_rows = []
    for phase, group in summary.groupby("phase", sort=False):
        shares = group.sort_values("gas_share_of_phase_sample", ascending=False)["gas_share_of_phase_sample"].to_numpy()
        concentration_rows.append({
            "phase": phase,
            "sampled_blocks": int(totals.loc[totals["phase"].eq(phase), "sampled_blocks"].iloc[0]),
            "top_1_gas_share": float(shares[:1].sum()),
            "top_5_gas_share": float(shares[:5].sum()),
            "top_10_gas_share": float(shares[:10].sum()),
            "hhi": float((shares ** 2).sum()),
        })
    concentration = pd.DataFrame(concentration_rows).set_index("phase").reindex(PHASE_ORDER).reset_index()
    composition_path = out_dir / "recipient_composition_top8_by_phase.csv"
    cross_path = out_dir / "recipient_cross_phase_candidates.csv"
    concentration_path = out_dir / "recipient_concentration_by_phase.csv"
    figure_path = out_dir / "polygon_phase_recipient_gas_composition.png"
    composition.to_csv(composition_path, index=False)
    cross.to_csv(cross_path, index=False)
    concentration.to_csv(concentration_path, index=False)
    _plot(composition, figure_path)
    manifest = {
        "input_scope": "局面別に無作為抽出したブロックのreceipt gasUsed。宛先はトップレベル取引のto。",
        "not_measured": "内部call先、プロトコル単位への集約、因果関係、全期間全ブロックの総量。",
        "top_n_per_phase": top_n_per_phase,
        "files": {
            "composition": str(composition_path),
            "cross_phase_candidates": str(cross_path),
            "concentration": str(concentration_path),
            "figure": str(figure_path),
        },
    }
    manifest_path = out_dir / "analysis_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["files"]["manifest"] = str(manifest_path)
    return {"composition": composition, "cross_phase_candidates": cross, "concentration": concentration, "manifest": manifest}
