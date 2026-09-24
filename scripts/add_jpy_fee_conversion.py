"""新JPYC直接送金の実費表へ、取引日UTCの日次USD/JPYと円建て実費を加える。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd


FRANKFURTER_API = "https://api.frankfurter.dev/v1"


def _fetch_usd_jpy(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """FrankfurterのUSD建て日次JPYレートを取得する。"""
    url = f"{FRANKFURTER_API}/{start.date()}..{end.date()}?base=USD&symbols=JPY"
    request = Request(
        url,
        headers={
            # 一部の公開APIは、Python標準ライブラリの既定User-Agentをbotとして拒否する。
            "User-Agent": "Mozilla/5.0 (compatible; Polygon-JPYC-fee-research/1.0)",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed public FX API endpoint.
        payload = json.load(response)
    rows = [
        {"fx_rate_date": pd.Timestamp(date, tz="UTC"), "usd_jpy": rates["JPY"]}
        for date, rates in payload["rates"].items()
        if "JPY" in rates
    ]
    if not rows:
        raise RuntimeError("USD/JPYの日次レートを取得できませんでした。")
    return pd.DataFrame(rows).sort_values("fx_rate_date")


def _summary(table: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for group, part in table.groupby("sample_group", sort=False):
        row: dict[str, object] = {
            "sample_group": group,
            "label": part["sample_group_label"].iloc[0],
            "n": len(part),
        }
        for column in ("fee_pol", "fee_usd", "fee_jpy"):
            values = pd.to_numeric(part[column], errors="coerce").dropna()
            for label, value in {
                "mean": values.mean(), "median": values.median(), "min": values.min(),
                "max": values.max(), "p25": values.quantile(0.25), "p75": values.quantile(0.75), "p95": values.quantile(0.95),
            }.items():
                row[f"{column}_{label}"] = value
        rows.append(row)
    return pd.DataFrame(rows)


def add_jpy_conversion(screened_csv: Path, out_dir: Path) -> dict[str, object]:
    """直接JPYC transferを対象に円建て実費と状態別の要約を保存する。"""
    table = pd.read_csv(screened_csv)
    table = table.loc[table["is_direct_jpyc_transfer"].astype(bool)].copy()
    if table.empty:
        raise ValueError("直接JPYC transferがありません。")
    table["receipt_utc_date"] = pd.to_datetime(table["receipt_utc_date"], utc=True).dt.normalize()
    start, end = table["receipt_utc_date"].min(), table["receipt_utc_date"].max()
    fx = _fetch_usd_jpy(start, end)

    # 休日は、その直前の公表営業日レートを使う。実際に使ったレートの日付を残す。
    left = table.sort_values("receipt_utc_date")
    table = pd.merge_asof(left, fx, left_on="receipt_utc_date", right_on="fx_rate_date", direction="backward")
    if table["usd_jpy"].isna().any():
        raise ValueError("取引日の前に利用できるUSD/JPYレートがありません。")
    table["fee_jpy"] = pd.to_numeric(table["fee_usd"], errors="raise") * table["usd_jpy"]
    table["fx_date_matches_transaction_date"] = table["receipt_utc_date"].eq(table["fx_rate_date"])
    summary = _summary(table)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fx_path = out_dir / "usd_jpy_daily_rates.csv"
    fees_path = out_dir / "jpyc_direct_transfer_fees_jpy.csv"
    summary_path = out_dir / "jpyc_direct_transfer_fee_summary_jpy.csv"
    manifest_path = out_dir / "jpy_fee_conversion_manifest.json"
    fx.to_csv(fx_path, index=False)
    table.to_csv(fees_path, index=False)
    summary.to_csv(summary_path, index=False)
    manifest = {
        "fx_source": "Frankfurter v1 API, base=USD, symbols=JPY",
        "fx_url": f"{FRANKFURTER_API}/{start.date()}..{end.date()}?base=USD&symbols=JPY",
        "conversion": "fee_jpy = fee_usd × usd_jpy。fee_usdは取引日の日次POL/USDを使った既存値。",
        "non_business_days": "取引日が休日でレートが無い場合、直前の公表営業日のUSD/JPYを使用し、fx_rate_date列に記録する。",
        "files": {"usd_jpy_rates": str(fx_path), "fee_table_jpy": str(fees_path), "summary_jpy": str(summary_path)},
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["files"]["manifest"] = str(manifest_path)
    print(f"USD/JPY: {start.date()} to {end.date()}")
    print(f"Saved: {summary_path}")
    return {"fees": table, "summary": summary, "fx": fx, "manifest": manifest}


if __name__ == "__main__" and "ipykernel" not in sys.modules:
    raise SystemExit("Jupyterではrun_add_jpy_fee_conversion.pyを%runしてください。")
