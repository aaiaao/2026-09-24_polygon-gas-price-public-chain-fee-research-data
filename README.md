# Polygon PoSのガス価格上昇とパブリックチェーン手数料の不確実性 — 検証データ

本リポジトリは、Polygon PoSにおける2025年1月1日から2026年9月22日までのガス価格、ブロックスペース利用、確認済みPolymarket直接宛先群、およびJPYC直接送金の費用標本を扱ったリサーチメモの検証用データ一式である。

## 何を再現できるか

- Polygonの日次平均gas priceが、2025年の低い水準から、2026年初の急騰を経て、より高い通常水準へ移ったこと
- Polygonがblock gas limitとブロック生成間隔を変更し、一秒あたりの最大gas枠を拡張したこと
- 三つの観測局面で全宛先のgasUsed上位を集計すると、確認済みPolymarket関連コントラクトが上位に現れること
- 2025年の平常期と2026年の平常期を比べると、確認済みPolymarket直接宛先5コントラクトの推定gasUsed/秒が増えていること
- JPYCコントラクトへの単純なERC-20 `transfer`の個別標本で、手数料の要約統計が局面ごとに異なること

本リポジトリが推定しないものは、Polymarketがガス価格を何gwei押し上げたかという因果効果である。ここで示すのは、確認済みの直接宛先に届いた取引のgasUsed構成と、その時系列上の変化である。内部callは対象外であり、Polymarket活動の全量推定でもない。

## 構成

- `data/`：日次指標、局面別要約、図表の元となる集計表、再現用ブロック標本
- `figures/`：リサーチメモに掲載した4図
- `scripts/`：主要な集計・図表作成スクリプト
- `PROVENANCE.md`：取得元、取得日時、RPC、期間、方法、限界
- `DATA_DICTIONARY.md`：公開データの列定義と対応関係
- `PRIVACY.md`：公開しないデータと理由

## 主要な結果を確認する順番

1. `figures/01_polygon_fee_states_timeline_capacity_milestones.png` と `data/polygon_daily_fee_states.csv` で、日次gas priceと一秒あたりの最大gas枠を確認する。
2. `figures/02_jpyc_direct_transfer_fee_by_gas_state.png` と `data/jpyc_direct_transfer_fee_summary_jpy.csv` で、JPYC直接送金の要約統計を確認する。
3. `data/phase_top_recipients_public.csv` で、三局面の全宛先集計における確認済みPolymarketコントラクトの順位・構成比を確認する。
4. `figures/03_polygon_daily_known_polymarket_recipient_share.png` と `data/polymarket_direct_group_daily_share.csv` で、5コントラクトの経路別構成を確認する。
5. `figures/04_polygon_polymarket_gas_usage_floor.png` と `data/polymarket_gas_usage_floor_panel.csv`、`data/polymarket_gas_usage_floor_summary.csv` で、平常期のgasUsed/秒を比較する。

## 再実行について

スクリプトはPython 3.11で実行した。主な依存関係は `pandas`、`numpy`、`matplotlib` である。オンチェーン再取得にはPolygon JSON-RPCが必要であり、調査ではTenderly Public Polygon JSON-RPCを用いた。取引ログ・receipt明細は配布せず、取得範囲、乱数シード、抽出済みブロック一覧、再取得スクリプトを固定して再現可能にしている。詳細は `PROVENANCE.md` と `DATA_DICTIONARY.md` を参照。

## 出典

日次ネットワーク指標は [PolygonScan Charts](https://polygonscan.com/charts)、オンチェーン標本は [Tenderly Public Polygon JSON-RPC](https://tenderly.co/)、為替は [Frankfurter API](https://www.frankfurter.app/) に基づく。Polygonの処理可能量変更については、[2026年2月の公式説明](https://polygon.technology/blog/more-transactions-more-capacity-polygon-is-constantly-upgrading-to-keep-up-with-demand)と、[2026年6月の公式説明](https://polygon.technology/blog/a-billion-fans-five-weeks-one-network-we-spent-six-months-preparing-polygon-chain-for-the-summers-biggest-sporting-event)を参照した。Polymarket V2移行については[公式案内](https://help.polymarket.com/en/articles/14762452-polymarket-exchange-upgrade-april-28-2026)を参照した。
