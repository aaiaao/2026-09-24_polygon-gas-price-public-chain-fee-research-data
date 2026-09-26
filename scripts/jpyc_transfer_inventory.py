"""新JPYCのTransfer履歴をPolygon JSON-RPCから全件収集する第1段階。

対象は新JPYC（0xE7C3D8C9a439feDe00D2600032D5dB0Be71C3c29）のみ。
旧前払式JPYC（0x431D5dfF03120AFA4bDf332c61A6e1766eF37BDB）は対象外。

Notebookセルでの実行例:

    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path.cwd() / "scripts"))
    from jpyc_transfer_inventory import run_inventory

    manifest = run_inventory(
        rpc_url="https://tenderly.rpc.polygon.community/",
        out_dir=Path("./data/jpyc_inventory"),
    )

この段階は全Transferログの件数・ブロック範囲を確定するだけで、全件の
transaction receipt（手数料）は取得しない。手数料は、比較期間を確定後に
標本だけを別スクリプトで取得する。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CONTRACT_ADDRESS = "0xE7C3D8C9a439feDe00D2600032D5dB0Be71C3c29"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
# 新JPYCの開始を取りこぼさないため、2025年8月1日 00:00 UTC から収集する。
# ブロック番号を固定せず、実行時にRPCからこの日時以降の最初のブロックを二分探索する。
DEFAULT_FROM_TIMESTAMP = 1_754_006_400
DEFAULT_FROM_DATE_LABEL = "2025-08-01T00:00:00Z"
# Tenderly公開RPCのeth_getLogs上限に合わせる。大き過ぎるとHTTP 413になる。
DEFAULT_BLOCK_SPAN = 25_000
# Polygonが案内する代替プロバイダの一つ。APIキー不要の公開エンドポイント。
DEFAULT_RPC_URL = "https://tenderly.rpc.polygon.community/"


class RpcError(RuntimeError):
    pass


def normalize_rpc_url(rpc_url: str) -> str:
    """誤って貼られたMarkdownリンクをURL本体に戻し、最低限の形式を検査する。"""
    value = rpc_url.strip()
    matched = re.fullmatch(r"\[[^\]]+\]\((https?://[^)]+)\)", value)
    if matched:
        value = matched.group(1)
    if not value.startswith(("https://", "http://")):
        raise ValueError("rpc_urlには https:// で始まるJSON-RPC URLを指定してください。")
    return value


def rpc_call(rpc_url: str, method: str, params: list[Any]) -> Any:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = Request(
        rpc_url,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "polygon-gas-research/1.0"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=90) as response:  # noqa: S310 - caller provides the intended RPC
            answer = json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RpcError(f"RPC HTTP error: {exc}") from exc
    if "error" in answer:
        raise RpcError(str(answer["error"]))
    if "result" not in answer:
        raise RpcError(f"RPC response has no result: {answer}")
    return answer["result"]


def first_block_at_or_after(rpc_url: str, timestamp: int, latest_block: int) -> int:
    """指定UTC時刻以降で最初のPolygonブロック番号をRPCから求める。"""
    low, high = 0, latest_block
    while low < high:
        middle = (low + high) // 2
        block = rpc_call(rpc_url, "eth_getBlockByNumber", [hex(middle), False])
        if block is None:
            raise RpcError(f"block {middle:,} could not be read while resolving the start date")
        if int(block["timestamp"], 16) < timestamp:
            low = middle + 1
        else:
            high = middle
    return low


def decode_log(log: dict[str, Any]) -> dict[str, Any]:
    topics = log["topics"]
    sender = "0x" + topics[1][-40:]
    recipient = "0x" + topics[2][-40:]
    sender, recipient = sender.lower(), recipient.lower()
    kind = "mint" if sender == ZERO_ADDRESS else "burn" if recipient == ZERO_ADDRESS else "transfer"
    return {
        "contract_address": log["address"].lower(),
        "block_number": int(log["blockNumber"], 16),
        "transaction_hash": log["transactionHash"].lower(),
        "transaction_index": int(log["transactionIndex"], 16),
        "log_index": int(log["logIndex"], 16),
        "from_address": sender,
        "to_address": recipient,
        "event_kind": kind,
        "token_amount_raw": str(int(log["data"], 16)),
    }


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def run_inventory(
    rpc_url: str,
    out_dir: Path,
    from_block: int | None = None,
    to_block: int | None = None,
    initial_block_span: int = DEFAULT_BLOCK_SPAN,
    pause_sec: float = 0.08,
    resume: bool = True,
) -> dict[str, Any]:
    """全Transferログを取得する。RPCの範囲制限時には自動で問い合わせ幅を縮める。"""
    rpc_url = normalize_rpc_url(rpc_url)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "jpyc_transfer_events_rpc.jsonl"
    state_path = out_dir / "jpyc_transfer_rpc_state.json"

    latest_block = int(rpc_call(rpc_url, "eth_blockNumber", []), 16)
    if from_block is None:
        from_block = first_block_at_or_after(rpc_url, DEFAULT_FROM_TIMESTAMP, latest_block)
        print(f"Start date {DEFAULT_FROM_DATE_LABEL} resolved to block {from_block:,}")
    final_block = latest_block if to_block is None else min(to_block, latest_block)
    state = {
        "rpc_url": rpc_url,
        "contract_address": CONTRACT_ADDRESS,
        "scope_note": "新JPYCのみ。旧前払式JPYC（0x431D5dfF03120AFA4bDf332c61A6e1766eF37BDB）は対象外。",
        "from_date_utc": DEFAULT_FROM_DATE_LABEL if from_block is not None else None,
        "from_block": from_block,
        "to_block": final_block,
        "next_block": from_block,
    }
    if resume and state_path.exists():
        prior = json.loads(state_path.read_text(encoding="utf-8"))
        same_query = all(prior.get(key) == state[key] for key in ("rpc_url", "contract_address", "from_block"))
        if to_block is not None:
            same_query = same_query and prior.get("to_block") == final_block
        if not same_query:
            raise ValueError("既存stateのRPC URLまたはブロック範囲が違います。別out_dirを使ってください。")
        # 「最新まで」は初回起動時点の最新ブロックに固定し、再開時に範囲が動かないようにする。
        final_block = int(prior["to_block"])
        state["to_block"] = final_block
        state["next_block"] = int(prior["next_block"])
        print(f"Resuming at block {state['next_block']:,}")

    print(f"Target end block: {final_block:,}")

    cursor = int(state["next_block"])
    active_block_span = initial_block_span
    while cursor <= final_block:
        span = min(active_block_span, final_block - cursor + 1)
        while True:
            query_end = min(cursor + span - 1, final_block)
            params = [{
                "fromBlock": hex(cursor),
                "toBlock": hex(query_end),
                "address": CONTRACT_ADDRESS,
                "topics": [TRANSFER_TOPIC],
            }]
            try:
                logs = rpc_call(rpc_url, "eth_getLogs", params)
                if not isinstance(logs, list):
                    raise RpcError(f"eth_getLogs returned non-list: {logs}")
                break
            except RpcError as exc:
                if span == 1:
                    raise RuntimeError(f"block {cursor:,} cannot be queried. Change RPC provider.\n{exc}") from exc
                span = max(1, span // 2)
                print(f"  range rejected; retrying {span:,} blocks: {exc}")

        events = [decode_log(log) for log in logs]
        active_block_span = span
        append_jsonl(raw_path, events)
        cursor = query_end + 1
        state["next_block"] = cursor
        save_state(state_path, state)
        print(f"  through block {query_end:,}: {len(events):,} events")
        time.sleep(pause_sec)

    events = load_jsonl(raw_path)
    events.sort(key=lambda row: (row["block_number"], row["transaction_index"], row["log_index"]))
    csv_path = out_dir / "jpyc_transfer_events_rpc.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fields = list(events[0]) if events else ["contract_address", "block_number", "transaction_hash"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(events)

    manifest = {
        **state,
        "latest_block_at_start": latest_block,
        "transfer_event_count": len(events),
        "unique_transaction_count": len({row["transaction_hash"] for row in events}),
        "event_kind_counts": dict(Counter(row["event_kind"] for row in events)),
        "files": {"raw_resumable": str(raw_path), "csv": str(csv_path), "state": str(state_path)},
        "next_step": "観測期間を決めてから、標本のtransaction receiptだけをRPCで取得し、effectiveGasPrice × gasUsedを計算する。",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nFinished.")
    print(f"Transfer events: {manifest['transfer_event_count']:,}")
    print(f"Unique transactions: {manifest['unique_transaction_count']:,}")
    print(f"Event kinds: {manifest['event_kind_counts']}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-url", default=DEFAULT_RPC_URL)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--from-block", type=int, help="省略時は2025-08-01 00:00 UTCから開始")
    parser.add_argument("--to-block", type=int)
    args = parser.parse_args()
    run_inventory(args.rpc_url, args.out_dir, args.from_block, args.to_block)


if __name__ == "__main__" and "ipykernel" not in sys.modules:
    main()
