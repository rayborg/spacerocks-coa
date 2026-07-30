from __future__ import annotations

import argparse
import asyncio

from scripts._operator import load_commands, opaque_id, require_confirmation


def main() -> int:
    parser = argparse.ArgumentParser(description="Request one pending proof upgrade")
    parser.add_argument("order_id", type=opaque_id)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    require_confirmation(args.confirm, f"UPGRADE:{args.order_id}")
    asyncio.run(load_commands().upgrade(args.order_id))
    print(f"event=upgrade_requested order_id={args.order_id}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
