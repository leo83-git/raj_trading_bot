"""Local compatibility shim for the optional discounts package.

This project does not depend on the sibling discounts package, but some
regression tests import ``discounts.main``. To keep this repository isolated,
we provide a lightweight local package that shadows any external
``discounts`` package discovered via the parent workspace path.
"""

try:
    from .main import _parse_args, generate_report
except Exception:  # pragma: no cover - defensive for test stubs
    from .main import generate_report

    _parse_args = None

__all__ = ["_parse_args", "generate_report"]
