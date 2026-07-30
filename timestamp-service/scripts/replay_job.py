from __future__ import annotations

import argparse
import asyncio

from scripts._operator import load_commands, opaque_id, require_confirmation


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay one durable timestamp job")
    parser.add_argument("job_id", type=opaque_id)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    require_confirmation(args.confirm, f"REPLAY:{args.job_id}")
    asyncio.run(load_commands().replay(args.job_id))
    print(f"event=replay_requested job_id={args.job_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
