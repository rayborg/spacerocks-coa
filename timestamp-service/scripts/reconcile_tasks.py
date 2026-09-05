from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta

from app.tasks.dispatch import MAX_STALE_DISPATCH_GRACE, MIN_STALE_DISPATCH_GRACE
from scripts._operator import load_commands


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile pending Cloud Tasks dispatch intents")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--recover-stale-dispatched",
        action="store_true",
        help="replace overdue dispatched tasks with a new job generation",
    )
    parser.add_argument(
        "--stale-grace-seconds",
        type=int,
        default=int(MIN_STALE_DISPATCH_GRACE.total_seconds()),
        help="minimum age after the job was due and dispatched before explicit recovery",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    stale_grace = timedelta(seconds=args.stale_grace_seconds)
    if not MIN_STALE_DISPATCH_GRACE <= stale_grace <= MAX_STALE_DISPATCH_GRACE:
        parser.error(
            "--stale-grace-seconds must be between "
            f"{int(MIN_STALE_DISPATCH_GRACE.total_seconds())} and "
            f"{int(MAX_STALE_DISPATCH_GRACE.total_seconds())}"
        )
    commands = load_commands()
    if args.recover_stale_dispatched:
        selected, dispatched = asyncio.run(commands.recover_stale_tasks(args.limit, stale_grace))
        print(
            "event=stale_tasks_recovered "
            f"selected={selected} dispatched={dispatched} stale_grace_seconds={args.stale_grace_seconds}"
        )
    else:
        selected, dispatched = asyncio.run(commands.reconcile_tasks(args.limit))
        print(f"event=tasks_reconciled selected={selected} dispatched={dispatched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
