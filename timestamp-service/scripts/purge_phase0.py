from __future__ import annotations

import argparse
import asyncio

from scripts._operator import load_commands, opaque_id, require_confirmation


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge synthetic Phase 0 operational data while retaining proofs")
    parser.add_argument("order_id", type=opaque_id)
    parser.add_argument("--synthetic-phase0", action="store_true", required=True)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    require_confirmation(args.confirm, f"PURGE-SYNTHETIC-KEEP-PROOFS:{args.order_id}")
    asyncio.run(load_commands().purge_synthetic(args.order_id, preserve_proofs=True))
    print(f"event=synthetic_purge_requested order_id={args.order_id} proofs=preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
