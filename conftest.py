"""Test configuration to ensure local project modules are importable.

Pytest sometimes modifies ``sys.path`` in a way that can cause top‑level
packages (like the ``screener`` package in this repository) to be shadowed by
similarly named packages installed in the virtual environment.  By inserting
the absolute path of the repository at the start of ``sys.path`` we guarantee
that imports such as ``import screener`` resolve to the local package.
"""

import os
import sys
import types
from pathlib import Path

# Resolve the absolute path of the repository root (the directory containing this
# ``conftest.py`` file) and prepend it to ``sys.path`` if it is not already
# present.  This ensures that test modules can import project packages without
# interference from external packages of the same name.
repo_root = os.path.abspath(os.path.dirname(__file__))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

# Keep tests isolated from any real PostgreSQL instance or writable-user
# assumptions on the host. The application now supports SQLAlchemy with a
# SQLite fallback, so we default the test process to a temporary SQLite file
# unless the user has already provided an explicit DATABASE_URL.
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = "sqlite:////tmp/raj_trading_bot_test.db"
if "SQLITE_FALLBACK_URL" not in os.environ:
    os.environ["SQLITE_FALLBACK_URL"] = "sqlite:////tmp/raj_trading_bot_test.db"

# Avoid any accidental webhook traffic during tests.
os.environ.pop("DISCORD_WEBHOOK_URL", None)
os.environ.pop("TELEGRAM_WEBHOOK_URL", None)
os.environ.pop("TELEGRAM_BOT_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT_ID", None)


# Keep this repository isolated from sibling projects that happen to live under
# the same parent directory.
parent_dir = os.path.dirname(repo_root)
if parent_dir in sys.path:
    sys.path.remove(parent_dir)

def _register_raj_trading_bot_package() -> None:
    """Expose ``main`` as ``raj_trading_bot.main`` for imports and patch targets."""
    if "raj_trading_bot.main" in sys.modules:
        return
    import main as main_module

    try:
        import raj_trading_bot
        pkg = raj_trading_bot
    except ImportError:
        pkg = types.ModuleType("raj_trading_bot")
        pkg.__path__ = [os.path.dirname(__file__)]
        sys.modules["raj_trading_bot"] = pkg
        
    pkg.main = main_module
    sys.modules["raj_trading_bot.main"] = main_module

_register_raj_trading_bot_package()

# Force the local shim package to win even if another ``discounts`` package was
# imported earlier in the Python process.
for module_name in ("discounts", "discounts.main"):
    sys.modules.pop(module_name, None)
