from __future__ import annotations

import argparse
import asyncio

from scripts._operator import load_commands, opaque_id, require_confirmation


def reverification_request_id(value: str) -> str:
    parsed = opaque_id(value)
    if len(parsed) > 32:
        raise ValueError("reverification_request_id_too_long")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Reverify one timestamp order")
    parser.add_argument("order_id", type=opaque_id)
    parser.add_argument("--request-id", required=True, type=reverification_request_id)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    require_confirmation(args.confirm, f"REVERIFY:{args.order_id}:{args.request_id}")
    asyncio.run(load_commands().reverify(args.order_id, args.request_id))
    print(f"event=reverify_requested order_id={args.order_id} request_id={args.request_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
