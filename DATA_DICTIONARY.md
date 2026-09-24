# データ辞書

| ファイル | 内容 | 個別アドレス | 主な利用箇所 |
| --- | --- | --- | --- |
| `polygon_daily_fee_states.csv` | Polygon全体の日次gas price、gasUsed、block gas limit、block time、容量・利用率、局面ラベル | なし | 図1 |
| `phase_top_recipients_public.csv` | 三局面における上位宛先の集計。本文で確認したコントラクトのみをラベル付きで掲載 | 確認済みコントラクトのみ | 表2 |
| `polymarket_direct_group_daily_share.csv` | 5コントラクトと「その他の宛先」の日次標本内gasUsed | 確認済みコントラクト群の名称のみ | 図3 |
| `polymarket_gas_usage_floor_panel.csv` | Polygon全体の日次gasUsed/秒、5コントラクトの推定分、比較期間ラベル | なし | 図4・表3 |
| `polymarket_gas_usage_floor_summary.csv` | 図4・表3の期間別要約統計 | なし | 表3 |
| `jpyc_direct_transfer_fee_summary_jpy.csv` | JPYC直接ERC-20 transferの費用要約 | なし | 図2・表1 |
| `usd_jpy_daily_rates.csv` | 円換算に使ったUSD/JPY日次値 | なし | 表1 |
| `sampled_blocks_all_days.csv` | 全630日×各日20ブロックの再現用ブロック番号標本 | なし | 図3・図4 |
| `reproducibility_parameters.json` | RPC、期間、乱数シード、JPYC標本の固定条件 | なし | 再取得 |

## 用語

- `gasUsed`：取引またはブロックで実際に消費されたgas量。
- `effectiveGasPrice`：取引が実際に支払った1 gasあたりの価格。
- `gas_used_per_sec`：日次gasUsedを日次の実測ブロック生成間隔で割った値。
- `gas_capacity_per_sec`：一つのブロックに入れられるgas上限を実測ブロック生成間隔で割った、一秒あたりの最大gas枠。
- `polymarket_direct_gas_share`：各日の20ブロック標本に含まれた全取引gasUsedに対する、確認済みPolymarket直接宛先5コントラクトのgasUsed比。
- `estimated_polymarket_direct_gas_used_per_sec`：`gas_used_per_sec × polymarket_direct_gas_share`。内部callを含まない直接宛先ベースの推定値。

個別取引・receipt・宛先別明細は含めない。`scripts/`、`sampled_blocks_all_days.csv`、`PROVENANCE.md`に固定した取得範囲と乱数シードにより、同じ公開RPCから再取得する。
