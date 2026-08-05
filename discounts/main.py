"""Local shim for the optional discounts module used by tests.

This module exists solely so imports such as ``from discounts.main import ...``
resolve within this repository and do not accidentally pull in the sibling
project under /home/rajasekhar/vibe-coding/discounts.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def _parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--report", dest="report", default="summary")
    return parser.parse_args(list(args or []))


def generate_report(*args, **kwargs):
    return {"status": "ok", "report": "local-shim"}
