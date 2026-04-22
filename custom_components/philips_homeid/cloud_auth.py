# Copyright (c) 2025-2026, Renaud Allard <renaud@allard.it>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
"""Philips Cloud authentication: OTP, OAuth browser flow, token management."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import secrets
import shutil
import subprocess
import sys
import urllib.parse
from base64 import urlsafe_b64encode
from typing import Any

import aiohttp

from .const import (
    GIGYA_API_KEY,
    GIGYA_API_URL,
    MOBILE_APP_REDIRECT_URI,
    OAUTH_CLIENT_ID,
    OIDC_AUTH_ENDPOINT,
    OIDC_TOKEN_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)

# OAuth scopes (full set from APK DiDaConstants)
OAUTH_SCOPES = (
    "openid profile email offline_access "
    "DI.Account.read DI.AccountProfile.read DI.AccountProfile.write "
    "DI.AccountGeneralConsent.read DI.AccountGeneralConsent.write "
    "DI.GeneralConsent.read DI.GeneralConsent.write "
    "VoiceProvider.read VoiceProvider.write "
    "subscriptions consent profile_extended "
    "DI.AccountSubscription.write DI.AccountSubscription.read"
)


class CloudAuthError(Exception):
    """Raised on cloud authentication failures."""


_ALPINE_PW_TARGET = "/tmp/playwright_lib"
_ALPINE_CHROMIUM = "/usr/bin/chromium-browser"

# Script executed in a subprocess to isolate Playwright from HA's process
# management (signal handlers, child process reaping).
_BROWSER_OAUTH_SCRIPT = """\
import sys, os, logging, secrets, urllib.parse
if logging.getLogger("{logger_name}").isEnabledFor(logging.DEBUG):
    os.environ["DEBUG"] = "pw:*"
from playwright.sync_api import sync_playwright

session_token = "{session_token}"
auth_url = "{auth_url}"
GIGYA_API_KEY = "{gigya_api_key}"
executable_path = "{executable_path}" or None

auth_code = None

with sync_playwright() as p:
    launch_args = {{
        "headless": True,
        "args": [
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--in-process-gpu",
            "--disable-gpu-compositing",
            "--disable-gpu-sandbox",
            "--disable-seccomp-filter-sandbox",
            "--single-process",
            "--js-flags=--max-old-space-size=128",
            "--disable-site-isolation-trials",
            "--disable-features=IsolateOrigins,site-per-process",{chromium_debug_args}
        ],
    }}
    if executable_path:
        launch_args["executable_path"] = executable_path
    browser = p.chromium.launch(**launch_args)
    page = browser.new_page()

    gmid = secrets.token_hex(16)
    page.context.add_cookies([
        {{"name": f"glt_{{GIGYA_API_KEY}}", "value": session_token,
          "domain": ".accounts.home.id", "path": "/"}},
        {{"name": f"gac_{{GIGYA_API_KEY}}", "value": session_token,
          "domain": ".accounts.home.id", "path": "/"}},
        {{"name": "gmid", "value": gmid,
          "domain": ".accounts.home.id", "path": "/"}},
        {{"name": "ucid", "value": gmid,
          "domain": ".accounts.home.id", "path": "/"}},
        {{"name": "hasGmid", "value": "ver4",
          "domain": ".accounts.home.id", "path": "/"}},
    ])

    def handle_response(response):
        global auth_code
        if "authorize/continue" in response.url and not auth_code:
            for header in response.headers_array():
                if header["name"].lower() == "location":
                    location = header["value"]
                    if "code=" in location:
                        parsed = urllib.parse.urlparse(location)
                        qs = urllib.parse.parse_qs(parsed.query)
                        codes = qs.get("code", [])
                        if codes:
                            auth_code = codes[0]

    page.on("response", handle_response)

    try:
        page.goto(auth_url, timeout=30000, wait_until="networkidle")
    except Exception:
        pass

    if not auth_code:
        for _ in range(10):
            page.wait_for_timeout(1000)
            if auth_code:
                break

    if not auth_code:
        browser.close()
        sys.exit(1)

    browser.close()

print(auth_code)
"""


class PhilipsCloudAuth:
    """Handles OTP login, OAuth browser flow, and token management."""

    def __init__(self) -> None:
        """Initialize the auth client."""
        self._session: aiohttp.ClientSession | None = None
        self._we_installed_playwright: bool = False
        self._alpine_install: bool = False

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the aiohttp session and clean up any leftover Playwright install."""
        if self._we_installed_playwright:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, PhilipsCloudAuth._uninstall_playwright)
            self._we_installed_playwright = False
        if self._session:
            await self._session.close()
            self._session = None

    async def request_otp(self, email: str) -> str:
        """Request OTP code to be sent to the user's email.

        Returns the vToken needed for OTP verification.
        """
        session = await self._get_session()
        url = f"{GIGYA_API_URL}/accounts.auth.otp.email.sendCode"
        params = {
            "email": email,
            "apiKey": GIGYA_API_KEY,
            "format": "json",
        }

        async with session.post(url, data=params) as resp:
            data = await resp.json(content_type=None)

        error_code = data.get("errorCode", -1)
        if error_code != 0:
            msg = data.get("errorMessage", "Unknown error")
            raise CloudAuthError(f"OTP request failed: {msg}")

        vtoken = data.get("vToken")
        if not vtoken:
            raise CloudAuthError("No vToken in OTP response")

        _LOGGER.debug("OTP sent to %s", email)
        return vtoken

    async def verify_otp(self, email: str, code: str, vtoken: str) -> str:
        """Verify the OTP code entered by the user.

        Returns the Gigya session token.
        """
        session = await self._get_session()
        url = f"{GIGYA_API_URL}/accounts.auth.otp.email.login"
        params = {
            "email": email,
            "code": code,
            "vToken": vtoken,
            "apiKey": GIGYA_API_KEY,
            "format": "json",
        }

        async with session.post(url, data=params) as resp:
            data = await resp.json(content_type=None)

        error_code = data.get("errorCode", -1)
        if error_code != 0:
            msg = data.get("errorMessage", "Unknown error")
            raise CloudAuthError(f"OTP verification failed: {msg}")

        session_token = data.get("sessionInfo", {}).get("cookieValue")
        if not session_token:
            raise CloudAuthError("No session token in OTP response")

        _LOGGER.debug("OTP verified for %s", email)
        return session_token

    async def async_install_playwright(self) -> bool:
        """Install Playwright asynchronously (runs in executor)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.install_playwright)

    async def get_oidc_tokens(self, session_token: str) -> dict[str, Any]:
        """Exchange Gigya session for OIDC tokens using headless browser.

        Uses PKCE flow with Playwright to execute the Gigya OAuth authorize
        page JavaScript, exactly matching the mobile app's AppAuth flow.

        Returns dict with access_token, refresh_token, id_token, expires_in.
        """
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = (
            urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(16)

        params = {
            "client_id": OAUTH_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": MOBILE_APP_REDIRECT_URI,
            "scope": OAUTH_SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        auth_url = f"{OIDC_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"

        uninstall = self._we_installed_playwright
        alpine = self._alpine_install
        _LOGGER.debug("OAuth params: alpine=%s, we_installed=%s", alpine, uninstall)
        loop = asyncio.get_running_loop()
        auth_code = await loop.run_in_executor(
            None,
            lambda: self._browser_oauth(session_token, auth_url, uninstall, alpine),
        )
        if uninstall:
            # _browser_oauth already removed Playwright in its finally block.
            self._we_installed_playwright = False

        if not auth_code:
            raise CloudAuthError(
                "Could not obtain authorization code. "
                "Please ensure you have used the Philips HomeID app "
                "with this account at least once."
            )

        gigya_code = auth_code[0] if isinstance(auth_code, tuple) else auth_code
        return await self._exchange_code(gigya_code, code_verifier)

    @staticmethod
    def _is_musl() -> bool:
        """Check if the system uses musl libc (e.g. Alpine Linux, HA Docker)."""
        try:
            import ctypes.util

            libc_path = ctypes.util.find_library("c")
            if libc_path and "musl" in libc_path:
                return True
        except Exception:
            pass
        try:
            if os.path.exists("/etc/alpine-release"):
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def check_playwright_platform() -> str | None:
        """Check if the current platform supports Playwright.

        Returns None if supported, or an error message if not.
        """
        plat = sys.platform
        machine = platform.machine().lower()

        if plat == "linux":
            if machine in ("x86_64", "aarch64"):
                return None
            return (
                f"Playwright does not support Linux {machine}. "
                "Cloud login requires Linux x86_64 or aarch64 (64-bit)."
            )
        if plat == "darwin":
            return None
        if plat == "win32":
            return None

        return f"Playwright does not support platform {plat}/{machine}."

    def install_playwright(self) -> bool:
        """Install Playwright and Chromium. Returns True on success."""
        try:
            import playwright  # noqa: F401

            _LOGGER.debug("Playwright already importable from %s", playwright.__file__)
            if not self._alpine_install and os.path.exists("/etc/alpine-release"):
                self._alpine_install = True
                _LOGGER.debug("Alpine detected, setting alpine flag")
            return True
        except ImportError:
            pass

        platform_error = PhilipsCloudAuth.check_playwright_platform()
        if platform_error:
            _LOGGER.error(platform_error)
            return False

        is_musl = self._is_musl()
        _LOGGER.debug("musl detected: %s", is_musl)
        if is_musl:
            return self._install_playwright_alpine()
        return self._install_playwright_glibc()

    def _install_playwright_glibc(self) -> bool:
        """Install Playwright on glibc systems (standard pip path)."""
        _LOGGER.info("Installing playwright for cloud authentication")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "playwright"],
                capture_output=True,
                check=True,
                timeout=120,
            )
            _LOGGER.debug("Playwright pip package installed, installing chromium")
            subprocess.run(
                ["playwright", "install", "chromium"],
                capture_output=True,
                check=True,
                timeout=300,
            )
            _LOGGER.info("Playwright and chromium installed successfully")
            self._we_installed_playwright = True
            return True
        except subprocess.CalledProcessError as err:
            stderr = (err.stderr or b"").decode(errors="replace").strip()
            _LOGGER.error("Failed to install playwright: %s", stderr)
            return False
        except (FileNotFoundError, TimeoutError):
            _LOGGER.exception("Failed to install playwright")
            return False

    def _install_playwright_alpine(self) -> bool:
        """Install Playwright on Alpine/musl using system Chromium and Node.js."""
        _LOGGER.info("Alpine/musl detected, installing system Chromium + Node.js")
        try:
            subprocess.run(
                ["apk", "add", "--no-cache", "chromium", "nodejs-current"],
                capture_output=True,
                check=True,
                timeout=120,
            )
            _LOGGER.debug("System chromium and node.js installed")

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-cache-dir",
                    "greenlet",
                    "pyee",
                ],
                capture_output=True,
                check=True,
                timeout=120,
            )
            _LOGGER.debug("greenlet and pyee installed")

            machine = platform.machine().lower()
            plat_tags = [
                f"manylinux1_{machine}",
                f"manylinux_2_17_{machine}",
            ]
            installed = False
            for plat_tag in plat_tags:
                pip_cmd = [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-cache-dir",
                    "--no-deps",
                    "--platform",
                    plat_tag,
                    "--only-binary=:all:",
                    "--target",
                    _ALPINE_PW_TARGET,
                    "playwright",
                ]
                result = subprocess.run(
                    pip_cmd,
                    capture_output=True,
                    timeout=120,
                )
                if result.returncode == 0:
                    installed = True
                    break
                _LOGGER.debug(
                    "pip install playwright with %s failed: %s",
                    plat_tag,
                    result.stderr.decode(errors="replace").strip(),
                )
            if not installed:
                _LOGGER.error(
                    "pip install playwright failed for all platform tags: %s",
                    ", ".join(plat_tags),
                )
                return False
            _LOGGER.debug("Playwright wheel installed to %s", _ALPINE_PW_TARGET)

            system_node = shutil.which("node")
            if not system_node:
                _LOGGER.error("System node.js not found after apk install")
                return False
            bundled_node = os.path.join(
                _ALPINE_PW_TARGET, "playwright", "driver", "node"
            )
            if os.path.exists(bundled_node):
                os.remove(bundled_node)
            os.symlink(system_node, bundled_node)
            _LOGGER.debug("Symlinked system node to %s", bundled_node)

            if _ALPINE_PW_TARGET not in sys.path:
                sys.path.insert(0, _ALPINE_PW_TARGET)

            import importlib

            importlib.invalidate_caches()
            import playwright  # noqa: F401

            _LOGGER.info(
                "Playwright installed via Alpine path (system Chromium + Node.js)"
            )
            self._we_installed_playwright = True
            self._alpine_install = True
            return True
        except subprocess.CalledProcessError as err:
            stderr = (err.stderr or b"").decode(errors="replace").strip()
            _LOGGER.error("Failed to install playwright on Alpine: %s", stderr)
            return False
        except (FileNotFoundError, TimeoutError):
            _LOGGER.exception("Failed to install playwright on Alpine")
            return False
        except ImportError:
            _LOGGER.error("Playwright installed but import failed")
            return False

    @staticmethod
    def _uninstall_playwright() -> None:
        """Uninstall Playwright and its browsers."""
        _LOGGER.info("Uninstalling playwright")
        try:
            if os.path.isdir(_ALPINE_PW_TARGET):
                shutil.rmtree(_ALPINE_PW_TARGET, ignore_errors=True)
                if _ALPINE_PW_TARGET in sys.path:
                    sys.path.remove(_ALPINE_PW_TARGET)
                _LOGGER.debug("Removed Alpine playwright target dir")
                return

            if shutil.which("playwright"):
                subprocess.run(
                    ["playwright", "uninstall"],
                    capture_output=True,
                    timeout=60,
                )
            subprocess.run(
                [sys.executable, "-m", "pip", "uninstall", "-y", "playwright"],
                capture_output=True,
                timeout=60,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, TimeoutError):
            _LOGGER.debug("Playwright uninstall failed (non-critical)")

    @staticmethod
    def _browser_oauth(
        session_token: str,
        auth_url: str,
        uninstall_after: bool = True,
        alpine: bool = False,
    ) -> str | None:
        """Run headless browser OAuth flow in a subprocess.

        Returns the Gigya authorization code string.
        """
        executable = _ALPINE_CHROMIUM if alpine else ""
        debug = _LOGGER.isEnabledFor(logging.DEBUG)
        chromium_debug = (
            '\n            "--enable-logging=stderr",\n            "--v=1",'
            if debug
            else ""
        )
        script = _BROWSER_OAUTH_SCRIPT.format(
            session_token=session_token,
            auth_url=auth_url,
            gigya_api_key=GIGYA_API_KEY,
            executable_path=executable,
            logger_name=__name__,
            chromium_debug_args=chromium_debug,
        )
        auth_code: str | None = None
        try:
            env = os.environ.copy()
            if _ALPINE_PW_TARGET not in (env.get("PYTHONPATH") or ""):
                env["PYTHONPATH"] = _ALPINE_PW_TARGET + ":" + env.get("PYTHONPATH", "")
            stderr_target: Any = subprocess.PIPE
            debug_log = "/tmp/playwright_debug.log"
            if debug:
                stderr_target = open(debug_log, "w")  # noqa: SIM115
            try:
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    stdout=subprocess.PIPE,
                    stderr=stderr_target,
                    text=True,
                    timeout=90,
                    env=env,
                )
            finally:
                if stderr_target is not subprocess.PIPE:
                    stderr_target.close()
            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                auth_code = lines[0] if lines else None
                if auth_code:
                    _LOGGER.info("Browser OAuth obtained Gigya auth code")
            else:
                stderr_msg = ""
                if debug:
                    stderr_msg = f", debug log at {debug_log}"
                elif result.stderr:
                    stderr_msg = f": {result.stderr.strip()[:500]}"
                _LOGGER.error(
                    "Browser OAuth subprocess failed (exit %d)%s",
                    result.returncode,
                    stderr_msg,
                )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            _LOGGER.exception("Browser OAuth subprocess error")
        finally:
            if uninstall_after:
                PhilipsCloudAuth._uninstall_playwright()

        return auth_code

    async def _exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        """Exchange authorization code for OIDC tokens."""
        session = await self._get_session()
        data = {
            "client_id": OAUTH_CLIENT_ID,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": MOBILE_APP_REDIRECT_URI,
            "code_verifier": code_verifier,
        }

        _LOGGER.debug("Exchanging auth code for tokens at %s", OIDC_TOKEN_ENDPOINT)
        async with session.post(OIDC_TOKEN_ENDPOINT, data=data) as resp:
            text = await resp.text()
            _LOGGER.debug("Token exchange response: HTTP %s", resp.status)
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                raise CloudAuthError(f"Token exchange response not JSON: {text[:200]}")

        if "access_token" not in result:
            error = result.get("error_description", result.get("error", "Unknown"))
            _LOGGER.debug("Token exchange error response: %s", text[:500])
            raise CloudAuthError(f"Token exchange failed: {error}")

        _LOGGER.debug(
            "OIDC tokens obtained (scopes: %s, expires_in: %s)",
            result.get("scope", "?"),
            result.get("expires_in", "?"),
        )
        return result

    async def refresh_tokens(self, refresh_token: str) -> dict[str, Any]:
        """Refresh OIDC tokens using the refresh token."""
        session = await self._get_session()
        data = {
            "client_id": OAUTH_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        async with session.post(OIDC_TOKEN_ENDPOINT, data=data) as resp:
            result = await resp.json(content_type=None)

        if "access_token" not in result:
            error = result.get("error_description", result.get("error", "Unknown"))
            raise CloudAuthError(f"Token refresh failed: {error}")

        return result
