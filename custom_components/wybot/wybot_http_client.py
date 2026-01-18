"""Library for interacting with the WyBot API."""

import hashlib
import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .const import TIMEOUT
from .wybot_models import DevicesResponse, Group, LoginResponse

_LOGGER = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 10.0

# Send the user/password to get the Token
AUTH_URL = "https://api.wybotpool.com/api/user/login"


# Get all pools on the account
POOLS_URL = "https://api.wybotpool.com/api/env/pool"

# Given a Pool ID, get all the devices and the status
# The end should append the user id
DEVICES_URL = "https://api.wybotpool.com/api/group/"

# Send commands
COMMAND_URL = "https://api.wybotpool.com/api/device/ao"

# User notification endpoint - may be used for presence registration
NOTIFICATION_URL = "https://api.wybotpool.com/api/user/notification"

DEFAULT_HEADER = {
    "Content-Type": "application/json",
    "User-Agent": "WYBOT/13 CFNetwork/1498.700.2 Darwin/23.6.0",
}


class WyBotHTTPClient:
    """Client for interacting with the WyBot API."""

    _token: str | None = None
    _user_id: str | None = None
    _password: str
    _username: str
    _session: requests.Session | None = None

    def __init__(self, username: str, password: str) -> None:
        """Init the wybot api."""
        self._username = username
        self._password = password
        self._setup_session()

    def _setup_session(self) -> None:
        """Set up HTTP session with retry strategy."""
        self._session = requests.Session()
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def authenticate(self) -> bool:
        """Test if we can authenticate with the host."""
        login_response = self.login()
        self._token = (
            login_response.metadata.token
            if login_response and login_response.metadata
            else None
        )
        self._user_id = (
            login_response.metadata.user_id
            if login_response and login_response.metadata
            else None
        )
        return self._token is not None

    def _refresh_token_if_needed(self) -> bool:
        """Refresh token if it's expired or missing."""
        if self._token is None or self._user_id is None:
            _LOGGER.debug("Token missing, re-authenticating")
            return self.authenticate()
        return True

    def login(self) -> LoginResponse | None:
        """Authenticate the user and retrieve a token with retry logic."""
        _LOGGER.debug("Grabbing a token with a user and password")
        if not self._password:
            _LOGGER.error("Password is not set")
            return None
        md5_hash = hashlib.md5()
        md5_hash.update(self._password.encode("utf-8"))
        md5_hex = md5_hash.hexdigest()
        auth_data = {
            "username": self._username,
            "password": md5_hex,
        }

        delay = INITIAL_RETRY_DELAY
        for attempt in range(MAX_RETRIES):
            try:
                response = self._session.post(
                    AUTH_URL,
                    json=auth_data,
                    headers=DEFAULT_HEADER,
                    allow_redirects=False,
                    timeout=TIMEOUT,
                )
                if response.status_code == 200:
                    json_response = response.json()
                    response.close()
                    return LoginResponse(**json_response)
                _LOGGER.warning(
                    "Login attempt %d failed with status %d: %s",
                    attempt + 1,
                    response.status_code,
                    response.text,
                )
                response.close()
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    _LOGGER.error(
                        "Error getting token after %d attempts: %s",
                        MAX_RETRIES,
                        response.text,
                    )
                    return None
            except requests.exceptions.Timeout as err:
                _LOGGER.warning("Login timeout on attempt %d: %s", attempt + 1, err)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    _LOGGER.error("Login timeout after %d attempts", MAX_RETRIES)
                    return None
            except requests.exceptions.RequestException as err:
                _LOGGER.warning(
                    "Login request error on attempt %d: %s", attempt + 1, err
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    _LOGGER.error(
                        "Error getting token after %d attempts: %s", MAX_RETRIES, err
                    )
                    return None
            except Exception as err:
                _LOGGER.error("Unexpected error during login: %s", err)
                return None

        return None

    def get_devices_and_status(self) -> DevicesResponse | None:
        """Grab all devices and statuses with retry logic and token refresh."""
        if not self._refresh_token_if_needed():
            _LOGGER.error("Failed to refresh token")
            return None

        if self._user_id is None:
            _LOGGER.error("User ID is not set")
            return None

        device_url = DEVICES_URL + str(self._user_id)
        _LOGGER.debug("Grabbing devices and statuses: %s", device_url)

        delay = INITIAL_RETRY_DELAY
        for attempt in range(MAX_RETRIES):
            try:
                response = self._session.get(
                    device_url,
                    headers={**DEFAULT_HEADER, "Authorization": f"token {self._token}"},
                    allow_redirects=False,
                    timeout=TIMEOUT,
                )

                if response.status_code == 200:
                    json_response = response.json()
                    response.close()
                    return DevicesResponse(**json_response)
                if response.status_code == 401:
                    # Token expired, try to refresh
                    _LOGGER.info("Token expired, refreshing authentication")
                    response.close()
                    if self.authenticate():
                        # Retry immediately after re-auth
                        continue
                    _LOGGER.error("Failed to refresh token after 401")
                    return None
                _LOGGER.warning(
                    "Get devices attempt %d failed with status %d: %s",
                    attempt + 1,
                    response.status_code,
                    response.text,
                )
                response.close()
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    _LOGGER.error(
                        "Error getting devices after %d attempts: %s",
                        MAX_RETRIES,
                        response.text,
                    )
                    return None
            except requests.exceptions.Timeout as err:
                _LOGGER.warning(
                    "Get devices timeout on attempt %d: %s", attempt + 1, err
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    _LOGGER.error("Get devices timeout after %d attempts", MAX_RETRIES)
                    return None
            except requests.exceptions.RequestException as err:
                _LOGGER.warning(
                    "Get devices request error on attempt %d: %s", attempt + 1, err
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    _LOGGER.error(
                        "Error getting devices after %d attempts: %s", MAX_RETRIES, err
                    )
                    return None
            except Exception as err:
                _LOGGER.error("Unexpected error getting devices: %s", err)
                return None

        return None

    def get_indexed_current_grouped_devices(self) -> dict[str, Group]:
        """Return a dictionary of devices indexed by the grouped device_id."""
        response = self.get_devices_and_status()
        if response is None:
            return {}
        return {group.id: group for group in response.metadata.groups}

    def register_presence(self) -> bool:
        """Register presence with the cloud server.

        This signals to the WyBot cloud that we're actively listening,
        which may help ensure MQTT messages are relayed when devices come online.
        Note: Devices still need to be woken up via the mobile app's BLE connection.
        """
        if not self._refresh_token_if_needed():
            _LOGGER.debug("Failed to refresh token for presence registration")
            return False

        if self._user_id is None:
            _LOGGER.debug("User ID not set for presence registration")
            return False

        # POST to notification endpoint with userId to register presence
        try:
            response = self._session.post(
                NOTIFICATION_URL,
                headers={**DEFAULT_HEADER, "Authorization": f"token {self._token}"},
                json={"userId": self._user_id},
                allow_redirects=False,
                timeout=TIMEOUT,
            )
            success = response.status_code == 200
            if success:
                _LOGGER.debug("Presence registered successfully with cloud server")
            else:
                _LOGGER.debug(
                    "Presence registration response: status=%d, body=%s",
                    response.status_code,
                    response.text[:200] if response.text else "empty",
                )
            response.close()
            return success
        except Exception as err:
            _LOGGER.debug("Presence registration failed: %s", err)
            return False
