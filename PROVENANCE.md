# 出所台帳

## 時刻の基準

- オンチェーン日付と日次集計の境界：UTC
- 取得日時：JST
- 調査の主な取得日：2026年9月24日

## データセット別の出所

| 公開ファイル／図表 | 一次出所 | 取得日時・範囲 | 公開物に含める再現材料 |
| --- | --- | --- | --- |
| `polygon_daily_fee_states.csv`、図1 | [PolygonScan Charts](https://polygonscan.com/charts) のCSV | 2026-09-24 07:46 JSTに取得。2025-01-01〜2026-09-22 UTCの日次値 | 日次統合表、図表、局面判定ロジック |
| `sampled_blocks_all_days.csv`、図3・図4 | Tenderly Public Polygon JSON-RPC `https://tenderly.rpc.polygon.community/` | 2026-09-24。2025-01-01〜2026-09-22 UTC、630日×各日20ブロック＝12,600ブロック | 日付・ブロック番号、乱数シード、集計済み日次表、再取得・集計コード |
| `phase_top_recipients_public.csv`、表2 | 同上 | 2026-09-24。図1の三局面から、各日20ブロックを抽出 | 上位宛先表、確認済みコントラクトのラベル |
| `polymarket_direct_group_daily_share.csv`、図3 | 同上 | 同上 | 5コントラクトとその他の宛先の集計値 |
| `polymarket_gas_usage_floor_panel.csv`、`polymarket_gas_usage_floor_summary.csv`、図4・表3 | 同上とPolygonScan日次表 | 同上 | 全630日パネルと、平常期338日・240日の要約統計 |
| `jpyc_direct_transfer_fee_summary_jpy.csv`、図2・表1 | Tenderly Public Polygon JSON-RPC | 2026-09-24。現行JPYCコントラクト `0xE7C3D8C9a439feDe00D2600032D5dB0Be71C3c29`、2025-08-01 UTC以降。直接ERC-20 `transfer`の抽出標本 | 取得範囲、乱数シード、標本化・receipt取得・円換算コード、要約統計 |
| `usd_jpy_daily_rates.csv` | [Frankfurter API](https://www.frankfurter.app/)（USD base / JPY quote） | 2026-09-24 12:44 JST | 使用した日次レート表 |

## PolygonScanの元CSV

日次統合表は、PolygonScan Chartsの次のCSVを結合して作成した。

- `export-AvgGasPrice.csv`
- `export-GasUsed.csv`
- `export-GasLimit.csv`
- `export-BlockTime.csv`
- `export-BlockCountRewards.csv`
- `export-TxGrowth.csv`
- `export-maticprice.csv`

元CSVはすべてPolygonScanの公開チャートから再取得できるため、重複配布はしない。

## 集計方法

再取得の固定条件（RPC、対象期間、ブロック番号、乱数シード、JPYC標本条件）は `data/reproducibility_parameters.json` に記録した。宛先別gasUsedは `sampled_blocks_all_days.csv` の12,600ブロックを用いれば、乱数生成を経ずに同じブロックから再計算できる。

### 取引手数料

```text
fee_POL = gasUsed × effectiveGasPrice
fee_JPY = fee_POL × POL/USD（日次）× USD/JPY（日次）
```

JPYC分析は、上記コントラクトを取引の最初の宛先として直接呼ぶERC-20 `transfer`に限定する。旧JPYC、router・smart wallet等を経由する操作は分析対象に含めない。全Transferログと個別取引の生データは配布せず、`jpyc_transfer_inventory.py`、`jpyc_fee_state_sample.py`、`jpyc_receipt_fee_analysis.py`で再取得する。

### 宛先別gasUsed

各取引のreceiptに記録された`gasUsed`を、その取引の最初の宛先`transaction.to`に割り当てる。実行途中で生じる内部callは個別に追跡しない。日次20ブロック標本におけるgasUsed構成比を、Polygon全体の日次`gas_used_per_sec`に掛けて、直接宛先群のgasUsed/秒を推定した。

## 限界

- 宛先分析は全ブロックの悉皆調査ではなく、日次層化標本である。
- 5コントラクトは確認済みの直接宛先であり、Polymarketの内部callや未ラベル宛先を含まない。
- この分解はgasUsed構成の観測であり、ガス価格への因果効果を何gwei単位で推定するものではない。
- PolygonScanの日次平均は、分単位・秒単位の短いスパイクを捉えない。
- PolygonScanの取引画面のUSD表示は閲覧時点の評価となる場合がある。JPYCの要約統計では取引日の日次POL/USDとUSD/JPYを用いた。
