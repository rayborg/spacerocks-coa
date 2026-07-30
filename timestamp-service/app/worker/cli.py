from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import sys
from collections.abc import Callable
from typing import cast

from app.worker.runner import Worker


def _load_factory(path: str) -> Callable[[], Worker]:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name.startswith("app.") or not attribute.isidentifier():
        raise RuntimeError("invalid_worker_factory")
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise RuntimeError("invalid_worker_factory")
    return cast(Callable[[], Worker], factory)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the durable timestamp worker")
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    factory_path = os.environ.get("TIMESTAMP_WORKER_FACTORY", "")
    if not factory_path:
        parser.error("TIMESTAMP_WORKER_FACTORY is required; the worker fails closed without durable adapters")
    try:
        worker = _load_factory(factory_path)()
        if arguments.once:
            asyncio.run(worker.run_once())
        else:
            asyncio.run(worker.run_forever())
    except Exception:
        print("event=worker_failed code=worker_runtime_failure", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
