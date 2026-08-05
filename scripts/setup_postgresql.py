#!/usr/bin/env python3
"""Bootstrap PostgreSQL for the trading bot.

This script:
- checks whether PostgreSQL is installed and reachable
- installs PostgreSQL on Debian/Ubuntu systems when missing
- starts/enables the service
- creates a database role and database for the bot
- writes DATABASE_URL to a .env file for the Python app to consume

It is designed for Ubuntu/Debian environments and requires sudo for install
and service-management steps.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import re
import pwd
from pathlib import Path
from urllib.parse import quote_plus


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def have_command(name: str) -> bool:
    return shutil.which(name) is not None


def require_sudo() -> str:
    sudo = shutil.which("sudo")
    if not sudo:
        raise RuntimeError("sudo is required to install and configure PostgreSQL")
    return sudo


def is_root() -> bool:
    return os.geteuid() == 0


def as_postgres_cmd() -> list[str]:
    """Build a command prefix that executes as the postgres system user."""
    try:
        pwd.getpwnam("postgres")
    except KeyError:
        raise RuntimeError(
            "The postgres system user does not exist. Install the PostgreSQL server package first."
        )

    if is_root():
        if have_command("runuser"):
            return ["runuser", "-u", "postgres", "--"]
        if have_command("su"):
            return ["su", "-", "postgres", "-c"]
        raise RuntimeError("Neither runuser nor su is available to switch to postgres user")

    sudo = require_sudo()
    return [sudo, "-u", "postgres", "--"]


def install_postgresql() -> None:
    sudo = require_sudo()
    run([sudo, "apt-get", "update"])
    run([sudo, "apt-get", "install", "-y", "postgresql", "postgresql-contrib"])
    run([sudo, "systemctl", "enable", "--now", "postgresql"])


def ensure_service_running() -> None:
    sudo = require_sudo()
    run([sudo, "systemctl", "enable", "--now", "postgresql"], check=False)
    run([sudo, "systemctl", "start", "postgresql"], check=False)


def psql_available() -> bool:
    try:
        run(["psql", "--version"])
        return True
    except Exception:
        return False


def quote_ident(name: str) -> str:
    """Safely quote a PostgreSQL identifier."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(
            f"Invalid PostgreSQL identifier: {name!r}. Use letters, digits, and underscores only."
        )
    return f'"{name}"'


def psql_exec(sql: str) -> subprocess.CompletedProcess[str]:
    """Run SQL through psql as the postgres superuser."""
    prefix = as_postgres_cmd()
    if prefix[:2] == ["su", "-"] and prefix[-1] == "-c":
        command = "psql -v ON_ERROR_STOP=1 -c " + repr(sql)
        return run(prefix + [command], check=False)
    return run(prefix + ["psql", "-v", "ON_ERROR_STOP=1", "-c", sql], check=False)


def db_exists(db_name: str) -> bool:
    query = f"SELECT 1 FROM pg_database WHERE datname = '{db_name}'"
    result = run(as_postgres_cmd() + ["psql", "-tAc", query], check=False)
    return result.returncode == 0 and result.stdout.strip() == "1"


def role_exists(role_name: str) -> bool:
    query = f"SELECT 1 FROM pg_roles WHERE rolname = '{role_name}'"
    result = run(as_postgres_cmd() + ["psql", "-tAc", query], check=False)
    return result.returncode == 0 and result.stdout.strip() == "1"


def create_role_and_db(db_name: str, user: str, password: str) -> None:
    try:
        pwd.getpwnam("postgres")
    except KeyError:
        print("postgres system user missing; installing PostgreSQL server package...")
        install_postgresql()

    db_ident = quote_ident(db_name)
    user_ident = quote_ident(user)

    if not role_exists(user):
        result = psql_exec(
            f"CREATE ROLE {user_ident} WITH LOGIN PASSWORD '{password}';"
        )
    else:
        result = psql_exec(
            f"ALTER ROLE {user_ident} WITH LOGIN PASSWORD '{password}';"
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create/update role {user}: {result.stderr.strip() or result.stdout.strip()}"
        )

    if not db_exists(db_name):
        result = psql_exec(f"CREATE DATABASE {db_ident} OWNER {user_ident};")
    else:
        result = psql_exec(f"ALTER DATABASE {db_ident} OWNER TO {user_ident};")
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create/update database {db_name}: {result.stderr.strip() or result.stdout.strip()}"
        )


def update_env_file(env_path: Path, database_url: str) -> None:
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    new_lines: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith("DATABASE_URL="):
            new_lines.append(f"DATABASE_URL={database_url}")
            replaced = True
        else:
            new_lines.append(line)

    if not replaced:
        new_lines.append(f"DATABASE_URL={database_url}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Install and configure PostgreSQL for the trading bot")
    parser.add_argument("--db-name", default=os.getenv("POSTGRES_DB", "trading_bot"))
    parser.add_argument("--db-user", default=os.getenv("POSTGRES_USER", "trading_bot"))
    parser.add_argument("--db-password", default=os.getenv("POSTGRES_PASSWORD", "trading_bot_password"))
    parser.add_argument("--host", default=os.getenv("POSTGRES_HOST", "localhost"))
    parser.add_argument("--port", default=os.getenv("POSTGRES_PORT", "5433"))
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()

    if not have_command("psql"):
        print("psql not found; installing PostgreSQL...")
        install_postgresql()
    else:
        print("PostgreSQL client detected.")
        ensure_service_running()

    if not psql_available():
        print("PostgreSQL still not available after install attempt.", file=sys.stderr)
        return 1

    create_role_and_db(args.db_name, args.db_user, args.db_password)
    encoded_user = quote_plus(args.db_user)
    encoded_password = quote_plus(args.db_password)
    encoded_db_name = quote_plus(args.db_name)
    database_url = (
        f"postgresql+psycopg2://{encoded_user}:{encoded_password}"
        f"@{args.host}:{args.port}/{encoded_db_name}"
    )
    update_env_file(Path(args.env_file), database_url)

    print("PostgreSQL is configured.")
    print(f"DATABASE_URL written to {args.env_file}")
    print(database_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
