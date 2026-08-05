# Lazy loader for the real ``config`` package.
#
# The test suite imports ``load_config`` via ``from config.config import load_config``.
# A top‑level ``config.py`` file shadows the ``config`` directory, preventing Python
# from treating it as a package.  To avoid altering the existing package layout,
# we provide a thin wrapper that loads the implementation directly from the
# ``config/config.py`` file on demand.

import importlib.util
import os
from typing import Any


def load_config(path: str) -> dict[str, Any]:
    """Load configuration from *path* using the real implementation.

    The function locates the ``config/config.py`` module relative to this file,
    loads it as a separate module, and forwards the call to its ``load_config``
    function.  This approach bypasses the normal package import mechanism, which
    would otherwise fail because this file masks the ``config`` package.
    """
    # Resolve the absolute path to the actual implementation module.
    base_dir = os.path.dirname(__file__)
    impl_path = os.path.join(base_dir, "config", "config.py")

    spec = importlib.util.spec_from_file_location(
        "_raj_trading_bot_config_impl", impl_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load config implementation from {impl_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]

    # The implementation module defines ``load_config``.
    return module.load_config(path)
