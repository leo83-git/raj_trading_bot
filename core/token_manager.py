"""
Zerodha Token Manager for OAuth flow and token persistence.
"""

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from kiteconnect import KiteConnect

from core.logger import get_logger

log = get_logger("token_manager")


class ZerodhaTokenManager:
    """Token manager for Zerodha OAuth flow"""

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.kite = KiteConnect(api_key=api_key)
        self.access_token = None
        self.token_expiry = None
        self.TOKEN_FILE = Path(__file__).parent.parent / "data" / "zerodha_token.json"

    def load_token(self) -> bool:
        """Load token from file if it exists and is valid.

        If the token file is missing or invalid, fall back to the
        ``ZERODHA_ACCESS_TOKEN`` environment variable (set via ``config.py``).
        The environment token is assumed to be valid for the current day and
        will be used without expiry checks – this mirrors the historic
        behaviour where users supplied a pre‑generated token manually.
        """
        # First, try the persisted token file.
        try:
            if self.TOKEN_FILE.exists():
                with open(self.TOKEN_FILE, "r") as f:
                    data = json.load(f)
                self.access_token = data.get("access_token")
                expiry_str = data.get("expiry")
                if expiry_str:
                    # Parse the ISO timestamp.
                    self.token_expiry = datetime.fromisoformat(expiry_str)
                    # Guard against absurdly distant expiries (e.g., the legacy
                    # placeholder "2099-12-31" that was historically used for
                    # testing). If the expiry is more than five years in the
                    # future, treat the token as expired to force a fresh OAuth.
                    if self.token_expiry.year > datetime.now().year + 5:
                        log.warning(
                            f"Cached Zerodha token expiry {self.token_expiry} is unrealistically far in the future – treating as expired."
                        )
                        self.token_expiry = None
                # If the token has an expiry and is past, treat it as invalid.
                # However, when the user explicitly opts to skip validation via
                # the ``ZERODHA_SKIP_TOKEN_VALIDATION`` environment variable, we
                # ignore expiry and trust the stored token.
                if self.token_expiry and datetime.now() >= self.token_expiry:
                    if os.getenv("ZERODHA_SKIP_TOKEN_VALIDATION") == "1":
                        log.info(
                            "Skipping token expiry check per ZERODHA_SKIP_TOKEN_VALIDATION – using cached token despite expiry"
                        )
                    else:
                        log.warning(
                            f"Cached Zerodha access token from {self.TOKEN_FILE} has expired (expiry: {self.token_expiry})."
                        )
                        # Invalidate the loaded token so that the caller will trigger OAuth.
                        self.access_token = None
                        self.token_expiry = None
                        return False
                # Token is present and not expired – optionally validate it.
                # By default we perform a lightweight validation by calling the
                # Zerodha ``profile`` endpoint. However, in environments where the
                # network call is undesirable (e.g., when the user has already
                # generated a valid token and wants to reuse it without an OAuth
                # round‑trip), they can set the environment variable
                # ``ZERODHA_SKIP_TOKEN_VALIDATION=1``. In that case we skip the API
                # call and trust the stored token.
                if self.access_token:
                    skip_validation = os.getenv("ZERODHA_SKIP_TOKEN_VALIDATION") == "1"
                    if skip_validation:
                        log.info(
                            f"Skipping token validation per ZERODHA_SKIP_TOKEN_VALIDATION – using cached token from {self.TOKEN_FILE}"
                        )
                        return True
                    # -----------------------------------------------------------------
                    # Lightweight validation – attempt to fetch the user profile using the
                    # loaded token. This call is inexpensive and will raise an exception
                    # if the token is rejected (e.g., expired, revoked, or malformed).
                    # -----------------------------------------------------------------
                    try:
                        # The KiteConnect client must have the access token set before
                        # any API call. ``set_access_token`` does not perform network I/O.
                        self.kite.set_access_token(self.access_token)
                        profile = self.kite.profile()
                        # ``profile`` should contain a ``user_id`` field for a valid token.
                        if profile and profile.get("user_id"):
                            log.info(
                                f"Validated access token from {self.TOKEN_FILE} (expiry: {self.token_expiry})"
                            )
                            return True
                        else:
                            log.warning(
                                "Loaded token appears invalid – profile response missing user_id."
                            )
                    except Exception as e:
                        # Any exception here indicates the token cannot be used.
                        log.warning(f"Stored Zerodha token validation failed: {e}")

                    # If we reach this point the token is considered invalid/expired.
                    # Clear it so that callers know a refresh is required.
                    self.access_token = None
                    self.token_expiry = None
                    return False
                else:
                    log.info(
                        f"No valid access token found in {self.TOKEN_FILE}. Requires new authentication."
                    )
        except json.JSONDecodeError:
            log.error(f"Error: Token file {self.TOKEN_FILE} is not valid JSON.")
        except Exception as e:
            log.error(f"Error loading token from {self.TOKEN_FILE}: {e}")

        # If file‑based token not usable, try environment variable.
        env_token = os.getenv("ZERODHA_ACCESS_TOKEN")
        if env_token and self._is_valid_access_token(env_token):
            self.access_token = env_token
            # No expiry information – assume valid for the session.
            self.token_expiry = None
            try:
                self.kite.set_access_token(self.access_token)
                profile = self.kite.profile()
                if profile and profile.get("user_id"):
                    log.info(
                        "Loaded access token from ZERODHA_ACCESS_TOKEN environment variable"
                    )
                    return True
                log.warning(
                    "Environment token present but Zerodha profile validation failed."
                )
            except Exception as e:
                log.warning(f"Failed to validate environment token: {e}")

        # No valid token found.
        self.access_token = None
        self.token_expiry = None
        return False

    def generate_access_token(self, request_token: str) -> bool:
        """Generates and saves the access token using the request token."""
        try:
            data = self.kite.generate_session(request_token, api_secret=self.api_secret)
            self.access_token = data["access_token"]
            self.token_expiry = datetime.now().replace(
                hour=23, minute=59, second=59, microsecond=0
            ) + timedelta(days=1)

            token_data = {
                "access_token": self.access_token,
                "expiry": self.token_expiry.isoformat(),
            }
            with open(self.TOKEN_FILE, "w") as f:
                json.dump(token_data, f, indent=4)
            self.kite.set_access_token(self.access_token)
            log.info(
                f"Successfully generated and saved new access token to {self.TOKEN_FILE} (expires: {self.token_expiry})."
            )
            return True
        except Exception as e:
            log.error(f"Error generating access token: {e}")
            self.access_token = None
            self.token_expiry = None
            return False

    def get_authorization_url(self) -> str:
        """Get the authorization URL for OAuth flow"""
        return self.kite.login_url()

    def request_access_token(self, request_token: str) -> bool:
        """Request access token using request token from OAuth flow"""
        try:
            data = self.kite.generate_session(request_token, api_secret=self.api_secret)
            self.access_token = data["access_token"]
            self.token_expiry = datetime.now() + timedelta(hours=1)

            token_data = {
                "access_token": self.access_token,
                "expiry": self.token_expiry.isoformat(),
            }
            os.makedirs(self.TOKEN_FILE.parent, exist_ok=True)
            with open(self.TOKEN_FILE, "w") as f:
                json.dump(token_data, f)
            self.kite.set_access_token(self.access_token)
            log.info(
                f"Successfully obtained access token (expires: {self.token_expiry})"
            )
            return True
        except Exception as e:
            log.error(f"Failed to obtain access token: {e}")
            return False

    def _is_valid_access_token(self, token: str) -> bool:
        """Validate that the token is properly formed for Zerodha"""
        if not token or not isinstance(token, str):
            return False
        if token.startswith("mock_") or token.startswith("dummy_"):
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9]{32,40}", token))


ZerodhaZerodhaTokenManager = ZerodhaTokenManager
