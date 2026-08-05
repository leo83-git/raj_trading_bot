#!/home/rajasekhar/vibe-coding/raj_trading_bot/.venv/bin/python3

# Add project root to Python path
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

"""
Setup Zerodha Access Token

This script helps you obtain and save your Zerodha access token for use with the trading system.

Usage:
1. Run this script: python scripts/setup_zerodha_token.py
2. It will display a login URL
3. Open the URL in your browser and log in to Zerodha
4. After login, you'll be redirected to a URL with a request_token parameter
5. Copy the request_token from the URL and paste it when prompted
6. The script will exchange the request_token for an access_token and save it
"""

import os
import sys
from pathlib import Path

from core.token_manager import ZerodhaTokenManager

# Load config to get API key and secret
config_path = os.path.join(os.path.dirname(__file__), "..", "config", "config.yaml")

try:
    import yaml

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    api_key = config.get("zerodha_api_key")
    api_secret = config.get("zerodha_api_secret")

    if not api_key or not api_secret:
        print("Error: Zerodha API key or secret not found in config.yaml")
        print("Please ensure you have the following in your config.yaml:")
        print("zerodha_api_key: your_api_key_here")
        print("zerodha_api_secret: your_api_secret_here")
        sys.exit(1)

except Exception as e:
    print(f"Error loading config: {e}")
    print("Please ensure config.yaml exists and has the required Zerodha credentials")
    sys.exit(1)

# Initialize token manager
token_manager = ZerodhaTokenManager(api_key, api_secret)

# ---------------------------------------------------------------------------
# If a valid token already exists, skip the interactive flow.
# ---------------------------------------------------------------------------
# Resolve the token file location relative to the project root.
# The original code had a stray quotation mark causing a syntax error.
token_file = Path(__file__).resolve().parents[2] / "data" / "zerodha_token.json"
if token_file.is_file():
    try:
        import json

        with token_file.open() as f:
            data = json.load(f)
        # Simple sanity check – ensure the token has an expiry field.
        if data.get("access_token") and data.get("expiry"):
            print("✅ Existing Zerodha access token found – no setup needed.")
            sys.exit(0)
    except Exception:
        # If reading fails, fall back to normal interactive flow.
        pass

print("=== Zerodha Access Token Setup ===\n")

# Generate login URL
login_url = token_manager.get_authorization_url()
print("1. Open this URL in your browser to log in to Zerodha:")
print(f" {login_url}\n")

print("2. After logging in, you'll be redirected to a URL like:")
print(" https://kite.zerodha.com/connect/login?request_token=YOUR_REQUEST_TOKEN_HERE\n")

print(
    "3. Copy the request_token value from the redirected URL (everything after request_token=)"
)

# Get request token from user
request_token = input("\n4. Paste your request_token here: ").strip()
if not request_token:
    print("Error: No request_token provided")
    sys.exit(1)

# Exchange request token for access token
print("\nExchanging request_token for access_token...")
if token_manager.request_access_token(
    request_token
):  # Changed from generate_session to request_access_token
    print("\n✅ Success! Access token has been saved to data/zerodha_token.json")
    print("You can now run your trading system without manual authentication.")
    print("The token will automatically refresh every 24 hours (expires at 6 AM IST).")
else:
    print("\n❌ Error: Failed to generate access token")
    print("Please check your request_token and try again.")
    sys.exit(1)

print("\nSetup complete!")
