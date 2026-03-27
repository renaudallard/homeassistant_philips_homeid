#!/usr/bin/env python3
# Copyright (c) 2025, Renaud Allard <renaud@allard.it>
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
"""
Fetch Philips HomeID device credentials from the cloud API.

Authenticates via email OTP + headless browser OAuth, then queries the
Home ID backend API (primary) and IoT API (fallback) for registered
devices and their local credentials.

Requirements:
  pip install playwright && playwright install chromium

Usage (interactive):
  python3 cloud_key_fetcher.py                    # Prompts for everything
  python3 cloud_key_fetcher.py user@example.com   # Prompts for OTP code

Usage (non-interactive):
  python3 cloud_key_fetcher.py user@example.com          # Send OTP
  python3 cloud_key_fetcher.py user@example.com 123456    # Verify + fetch
  python3 cloud_key_fetcher.py --resume                   # Reuse saved tokens
"""

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import ssl
import time
import urllib.parse
import urllib.request

# Gigya CDC OIDC
GIGYA_API_KEY = "4_JGZWlP8eQHpEqkvQElolbA"
GIGYA_API_URL = "https://cdc.accounts.home.id"
OIDC_ISSUER = f"{GIGYA_API_URL}/oidc/op/v1.0/{GIGYA_API_KEY}"
OAUTH_CLIENT_ID = "-u6aTznrxp9_9e_0a57CpvEG"
REDIRECT_URI = "com.philips.ka.oneka.app.prod://oauthredirect"

# HSDP IAM (from APK DomainBuilderKt / BackendConfigKt)
HSDP_IAM_URL = "https://iam-service.eu-west.philips-healthsuite.com"
HSDP_CLIENT_ID = "21e431131cb04a0eb56"
HSDP_CLIENT_SECRET = "@@3f2.6lo21_2F61"
HSDP_REDIRECT_URI = "com.philips.apps.nutriu.21e431131cb04a0eb56://oauthredirect"
HSDP_REDIRECT_PREFIX = HSDP_REDIRECT_URI.split("://")[0] + "://"

# IoT API (production, from APK DomainConfig)
IOT_BASE = "https://prod.eu-da.iot.versuni.com/api/da"

# Home ID backend (from APK BackendConfigKt)
BACKEND_BASE = "https://www.backend.vbs.versuni.com"
BACKEND_API_BASE = "https://www.backend.vbs.versuni.com/api"
HOMEID_ACCEPT = "application/vnd.oneka.v2.0+json"
HOMEID_USER_AGENT = (
    "HomeID/8.16.0 (com.philips.ka.oneka.app; build:8160001; Android 14)"
)
HOMEID_X_USER_AGENT = "Android 14;8.16.0"

# OAuth scopes (full set from APK)
SCOPES = (
    "openid profile email offline_access "
    "DI.Account.read DI.AccountProfile.read DI.AccountProfile.write "
    "DI.AccountGeneralConsent.read DI.AccountGeneralConsent.write "
    "DI.GeneralConsent.read DI.GeneralConsent.write "
    "VoiceProvider.read VoiceProvider.write "
    "subscriptions consents profile_extended "
    "DI.AccountSubscription.write DI.AccountSubscription.read"
)

STATE_FILE = "/tmp/philips_cloud_state.json"
DEBUG = False


def api_request(url, data=None, headers=None, method=None):
    """Make HTTP request and return (status, body)."""
    if data and isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
    elif data and isinstance(data, str):
        data = data.encode()

    req = urllib.request.Request(url, data=data, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    if DEBUG:
        m = method or ("POST" if data else "GET")
        print(f"  [DEBUG] {m} {url}")
        if headers:
            for k, v in headers.items():
                # Redact tokens
                val = v
                if "bearer" in k.lower() or "authorization" in k.lower():
                    val = v[:30] + "..." if len(v) > 30 else v
                print(f"  [DEBUG]   {k}: {val}")
        if data:
            body_str = data.decode() if isinstance(data, bytes) else str(data)
            # Redact long values
            if len(body_str) > 200:
                print(f"  [DEBUG]   Body: {body_str[:200]}...")
            else:
                print(f"  [DEBUG]   Body: {body_str}")

    ctx = ssl.create_default_context()
    try:
        resp = urllib.request.urlopen(req, timeout=30, context=ctx)
        body = resp.read().decode()
        if DEBUG:
            print(f"  [DEBUG]   -> {resp.status} ({len(body)} bytes)")
            if len(body) < 500:
                print(f"  [DEBUG]   -> {body}")
        try:
            return resp.status, json.loads(body)
        except json.JSONDecodeError:
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if DEBUG:
            print(f"  [DEBUG]   -> HTTP {e.code}: {body[:300]}")
        try:
            return e.code, json.loads(body)
        except (json.JSONDecodeError, AttributeError):
            return e.code, body


def request_otp(email):
    """Send OTP to email address. Returns vToken."""
    status, body = api_request(
        f"{GIGYA_API_URL}/accounts.auth.otp.email.sendCode",
        data={"email": email, "apiKey": GIGYA_API_KEY, "format": "json"},
    )
    if not isinstance(body, dict) or body.get("errorCode") != 0:
        raise RuntimeError(f"OTP request failed: {body}")
    vtoken = body.get("vToken")
    if not vtoken:
        raise RuntimeError("No vToken in OTP response")
    return vtoken


def verify_otp(email, code, vtoken):
    """Verify OTP and return session token."""
    status, body = api_request(
        f"{GIGYA_API_URL}/accounts.auth.otp.email.login",
        data={
            "email": email,
            "code": code,
            "vToken": vtoken,
            "apiKey": GIGYA_API_KEY,
            "format": "json",
        },
    )
    if not isinstance(body, dict) or body.get("errorCode") != 0:
        raise RuntimeError(f"OTP verification failed: {body}")
    return body["sessionInfo"]["cookieValue"]


def headless_oauth(session_token, email=None):
    """Run headless browser OAuth flow. Returns OIDC tokens dict."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed.")
        print("  pip install playwright && playwright install chromium")
        return None

    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    state = secrets.token_urlsafe(16)

    params = {
        "client_id": OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{OIDC_ISSUER}/authorize?{urllib.parse.urlencode(params)}"

    auth_code = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()

        # Set Gigya session cookies
        gmid = secrets.token_hex(16)
        context.add_cookies(
            [
                {
                    "name": f"glt_{GIGYA_API_KEY}",
                    "value": session_token,
                    "domain": ".accounts.home.id",
                    "path": "/",
                },
                {
                    "name": f"gac_{GIGYA_API_KEY}",
                    "value": session_token,
                    "domain": ".accounts.home.id",
                    "path": "/",
                },
                {
                    "name": "gmid",
                    "value": gmid,
                    "domain": ".accounts.home.id",
                    "path": "/",
                },
                {
                    "name": "ucid",
                    "value": gmid,
                    "domain": ".accounts.home.id",
                    "path": "/",
                },
                {
                    "name": "hasGmid",
                    "value": "ver4",
                    "domain": ".accounts.home.id",
                    "path": "/",
                },
            ]
        )

        page = context.new_page()

        def handle_response(response):
            nonlocal auth_code
            if "authorize/continue" in response.url and not auth_code:
                for header in response.headers_array():
                    if header["name"].lower() == "location":
                        location = header["value"]
                        if "code=" in location:
                            parsed = urllib.parse.urlparse(location)
                            qs = urllib.parse.parse_qs(parsed.query)
                            auth_code = qs.get("code", [None])[0]

        page.on("response", handle_response)

        try:
            page.goto(auth_url, timeout=30000, wait_until="networkidle")
        except Exception:
            pass

        if not auth_code:
            # Wait a bit more for JS to complete
            for _ in range(10):
                page.wait_for_timeout(1000)
                if auth_code:
                    break

        if not auth_code:
            browser.close()
            return None

        print("  Gigya auth code obtained")

        # Phase 2: HSDP token bridge (same browser, Gigya cookies active)
        hsdp_code = None
        hsdp_auth_url = (
            f"{HSDP_IAM_URL}/authorize/oidc/login?"
            f"api-version=1&provider=myphilipsonprod"
            f"&client_id={HSDP_CLIENT_ID}"
            f"&redirect_uri={HSDP_REDIRECT_URI}"
            f"&response_type=code"
        )

        def handle_hsdp_response(response):
            nonlocal hsdp_code
            if hsdp_code:
                return
            for header in response.headers_array():
                if header["name"].lower() == "location":
                    location = header["value"]
                    if HSDP_REDIRECT_PREFIX in location and "code=" in location:
                        parsed = urllib.parse.urlparse(location)
                        qs = urllib.parse.parse_qs(parsed.query)
                        codes = qs.get("code", [])
                        if codes:
                            hsdp_code = codes[0]

        print("  Navigating to HSDP authorize (SSO via Gigya cookies)...")
        if DEBUG:
            print(f"  [DEBUG] Full HSDP URL: {hsdp_auth_url}")
            # Dump all cookies the browser has
            cookies = context.cookies()
            print(f"  [DEBUG] Browser has {len(cookies)} cookies:")
            for c in cookies:
                print(f"  [DEBUG]   {c['domain']} {c['name']}={c['value'][:20]}...")
        page2 = context.new_page()

        def handle_hsdp_all(response):
            """Log all responses during HSDP flow."""
            url = response.url
            status_code = response.status
            loc = ""
            for h in response.headers_array():
                if h["name"].lower() == "location":
                    loc = h["value"]
            if DEBUG:
                # Log every single response
                if loc:
                    print(f"    [DEBUG] {status_code} {url[:100]}")
                    print(f"    [DEBUG]   -> Location: {loc[:200]}")
                else:
                    print(f"    [DEBUG] {status_code} {url[:100]}")
            elif status_code >= 300 and status_code < 400:
                print(f"    {status_code} {url[:80]} -> {loc[:120]}")

        page2.on("response", handle_hsdp_all)
        page2.on("response", handle_hsdp_response)

        if DEBUG:
            # Also log navigation events
            page2.on(
                "framenavigated",
                lambda frame: print(f"    [DEBUG] Frame navigated: {frame.url[:120]}")
                if frame == page2.main_frame
                else None,
            )
            page2.on(
                "request",
                lambda req: print(f"    [DEBUG] Request: {req.method} {req.url[:120]}"),
            )

        try:
            page2.goto(hsdp_auth_url, timeout=30000, wait_until="networkidle")
        except Exception as e:
            print(f"  HSDP navigation ended: {type(e).__name__}: {str(e)[:200]}")

        final_url = page2.url
        print(f"  Final page URL: {final_url[:200]}")

        # If we landed on accounts.philips.com login page, fill in credentials
        if "accounts.philips.com" in final_url and not hsdp_code:
            print("  Landed on accounts.philips.com login page")
            email_field = page2.query_selector("#capture_signIn_signInEmailAddress")
            pw_field = page2.query_selector("#capture_signIn_passwordSignIn")
            if email_field and pw_field and email_field.is_visible():
                import getpass

                philips_pw = getpass.getpass(
                    "  Enter your accounts.philips.com password: "
                )
                if philips_pw:
                    email_field.click()
                    page2.keyboard.type(email, delay=30)
                    page2.wait_for_timeout(300)
                    pw_field.click()
                    page2.keyboard.type(philips_pw, delay=30)
                    page2.wait_for_timeout(300)
                    login_btn = page2.locator("button:visible:has-text('Log in')")
                    login_btn.click()
                    print("  Submitted login, waiting for redirect...")
                    # Wait for the HSDP redirect chain to complete
                    for _ in range(30):
                        page2.wait_for_timeout(1000)
                        if hsdp_code:
                            break

        if not hsdp_code:
            for _ in range(10):
                page2.wait_for_timeout(1000)
                if hsdp_code:
                    break
                cur = page2.url
                if "code=" in cur:
                    parsed = urllib.parse.urlparse(cur)
                    qs = urllib.parse.parse_qs(parsed.query)
                    codes = qs.get("code", [])
                    if codes:
                        hsdp_code = codes[0]
                        print("  Got HSDP code from page URL")
                        break

        browser.close()

    if not auth_code:
        return None

    # Exchange Gigya code for OIDC tokens
    status, body = api_request(
        f"{OIDC_ISSUER}/token",
        data={
            "client_id": OAUTH_CLIENT_ID,
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if status != 200 or not isinstance(body, dict):
        print(f"  Gigya token exchange failed: {body}")
        return None

    print("  Gigya OIDC tokens obtained")

    # Exchange HSDP code for HSDP tokens
    if hsdp_code:
        print("  HSDP auth code obtained, exchanging for HSDP tokens...")
        hsdp_tokens = exchange_hsdp_code(hsdp_code)
        if hsdp_tokens:
            body["hsdp_access_token"] = hsdp_tokens.get("access_token", "")
            body["hsdp_refresh_token"] = hsdp_tokens.get("refresh_token", "")
            body["hsdp_id_token"] = hsdp_tokens.get("id_token", "")
            print("  HSDP tokens obtained!")
        else:
            print("  HSDP token exchange failed")
    else:
        print("  No HSDP auth code (SSO may not have worked)")

    return body


def exchange_hsdp_code(code):
    """Exchange HSDP authorization code for HSDP tokens."""
    # APK: raw concatenation, no URL encoding
    data = f"code={code}&grant_type=authorization_code&redirect_uri={HSDP_REDIRECT_URI}"
    # HSDP IAM requires HTTP Basic auth
    creds = base64.b64encode(f"{HSDP_CLIENT_ID}:{HSDP_CLIENT_SECRET}".encode()).decode()
    status, body = api_request(
        f"{HSDP_IAM_URL}/authorize/oauth2/token",
        data=data,
        headers={
            "Api-version": "2",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "Authorization": f"Basic {creds}",
        },
    )
    if status != 200 or not isinstance(body, dict):
        print(f"    HSDP token exchange failed: HTTP {status}, {body}")
        return None
    if "access_token" not in body:
        print(f"    HSDP response has no access_token: {body}")
        return None
    print(f"    HSDP token type: {body.get('token_type', '?')}")
    print(f"    HSDP expires_in: {body.get('expires_in', '?')}")
    # Decode sub claim from HSDP access token
    parts = body.get("access_token", "").split(".")
    if len(parts) >= 2:
        try:
            padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded))
            print(f"    HSDP sub (userId): {payload.get('sub', '?')}")
        except Exception:
            print("    HSDP access_token is not a JWT (opaque token)")
    return body


def refresh_hsdp_tokens(refresh_token):
    """Refresh HSDP tokens."""
    data = f"grant_type=refresh_token&refresh_token={refresh_token}"
    creds = base64.b64encode(f"{HSDP_CLIENT_ID}:{HSDP_CLIENT_SECRET}".encode()).decode()
    status, body = api_request(
        f"{HSDP_IAM_URL}/authorize/oauth2/token",
        data=data,
        headers={
            "Api-version": "2",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "Authorization": f"Basic {creds}",
        },
    )
    if status != 200 or not isinstance(body, dict):
        print(f"    HSDP refresh failed: HTTP {status}, {body}")
        return None
    if "access_token" not in body:
        print(f"    HSDP refresh has no access_token: {body}")
        return None
    return body


def test_mqtt_connection(access_token, thing_name=None, custom_sig=None):
    """Test actual MQTT connection to AWS IoT."""
    import socket as _socket
    import struct as _struct

    if custom_sig:
        sig = custom_sig
    else:
        # Get signature
        status, body = api_request(
            f"{IOT_BASE}/user/self/signature",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
        if status != 200 or not isinstance(body, dict):
            print(f"    Signature failed: HTTP {status}")
            return False
        sig = body.get("signature", "")

    # Decode sub for client ID. Keep FULL sub (including @fed-... suffix)
    # because the Custom Authorizer validates client ID matches token sub.
    # Use short hex suffix instead of UUID to stay under 128 char AWS IoT limit.
    parts = access_token.split(".")
    if len(parts) >= 2:
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        sub = json.loads(base64.urlsafe_b64decode(padded)).get("sub", "test")
    else:
        sub = "test"

    client_id = f"{sub}_{secrets.token_hex(4)}"
    print(f"    Client ID: {client_id[:60]}... ({len(client_id)} chars)")

    # Raw WebSocket + MQTT (matching iOS app headers exactly)
    ctx = ssl.create_default_context()
    host = "ats.prod.eu-da.iot.versuni.com"
    sock = _socket.create_connection((host, 443), timeout=15)
    sock = ctx.wrap_socket(sock, server_hostname=host)

    ws_key = base64.b64encode(os.urandom(16)).decode()
    # Match iOS app capture exactly (every header, same order)
    upgrade = (
        f"GET /mqtt HTTP/1.1\r\n"
        f"Host: {host}:443\r\n"
        f"Upgrade: websocket\r\n"
        f"x-amz-customauthorizer-signature: {sig}\r\n"
        f"Accept: */*\r\n"
        f"Sec-WebSocket-Key: {ws_key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"tenant: da\r\n"
        f"Sec-WebSocket-Protocol: mqtt\r\n"
        f"token-header: Bearer {access_token}\r\n"
        f"Accept-Language: en-US,en;q=0.9\r\n"
        f"x-amz-customauthorizer-name: CustomAuthorizer\r\n"
        f"Connection: Upgrade\r\n"
        f"Accept-Encoding: gzip, deflate, br\r\n"
        f"User-Agent: NutriU/5 CFNetwork/3860.300.31 Darwin/25.2.0\r\n"
        f"Content-Type: application/json\r\n"
        f"\r\n"
    )
    sock.send(upgrade.encode())

    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += sock.recv(1)

    if b"101" not in resp:
        print(f"    WebSocket upgrade failed: {resp.decode()[:100]}")
        sock.close()
        return False

    # MQTT CONNECT
    proto = b"\x00\x04MQTT\x04\x00\x00\x1e"
    cid = client_id.encode()
    payload = _struct.pack("!H", len(cid)) + cid
    remaining = proto + payload
    rl = len(remaining)
    rem_enc = b""
    while True:
        byte = rl & 0x7F
        rl >>= 7
        if rl > 0:
            byte |= 0x80
        rem_enc += bytes([byte])
        if rl == 0:
            break
    mqtt_pkt = b"\x10" + rem_enc + remaining
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(mqtt_pkt))
    if len(mqtt_pkt) < 126:
        frame = bytes([0x82, 0x80 | len(mqtt_pkt)]) + mask + masked
    else:
        frame = (
            bytes([0x82, 0x80 | 126])
            + _struct.pack("!H", len(mqtt_pkt))
            + mask
            + masked
        )
    sock.send(frame)
    print(f"    MQTT CONNECT sent, client_id={client_id[:40]}...")

    try:
        sock.settimeout(10)
        data = sock.recv(1024)
        if data and len(data) >= 2:
            if data[0] == 0x82 or data[0] == 0x02:
                plen = data[1] & 0x7F
                pstart = 2
                if plen == 126:
                    plen = _struct.unpack("!H", data[2:4])[0]
                    pstart = 4
                mqtt_resp = data[pstart : pstart + plen]
                if mqtt_resp and mqtt_resp[0] == 0x20:
                    rc = mqtt_resp[3]
                    if rc == 0:
                        print("    *** MQTT CONNECTED SUCCESSFULLY! ***")
                        sock.close()
                        return True
                    else:
                        print(f"    CONNACK rejected: return_code={rc}")
                else:
                    print(
                        f"    Unexpected MQTT packet: type={mqtt_resp[0] >> 4 if mqtt_resp else '?'}"
                    )
            elif data[0] == 0x88:
                code = _struct.unpack("!H", data[2:4])[0] if len(data) >= 4 else 0
                print(f"    WebSocket CLOSE (code={code}) - Custom Authorizer denied")
        else:
            print("    Connection closed")
    except _socket.timeout:
        print("    No response in 10s")
    sock.close()
    return False


def test_mqtt_signature(access_token):
    """Test the MQTT signature endpoint with an access token."""
    status, body = api_request(
        f"{IOT_BASE}/user/self/signature",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    print(f"    Signature endpoint: HTTP {status}")
    if status == 200 and isinstance(body, dict):
        print(f"    Response keys: {list(body.keys())}")
        sig = body.get("signature", "")
        print(
            f"    Signature: {sig[:20]}...{sig[-10:]}"
            if len(sig) > 30
            else f"    Signature: {sig}"
        )
        return body
    else:
        print(f"    Response: {body}")
        return None


# Home ID backend API (primary method)


def backend_login(oidc_tokens, email):
    """Login to Home ID backend. Returns (backend_token, discovery) or (None, {})."""
    id_token = oidc_tokens.get("id_token", "")
    if not id_token:
        print("  No id_token available, skipping backend login")
        return None, {}

    jsonapi_headers = {
        "Content-Type": "application/vnd.api+json",
        "Accept": "application/vnd.api+json",
        "User-Agent": HOMEID_USER_AGENT,
        "X-USER-AGENT": HOMEID_X_USER_AGENT,
        "Accept-Language": "en-GB",
    }

    # Step 1: Discovery
    discovery_url = f"{BACKEND_BASE}/.well-known/tenant/oneka"
    print(f"  Discovery: GET {discovery_url}")
    status, discovery = api_request(discovery_url)
    if status != 200 or not isinstance(discovery, dict):
        print(f"  Discovery failed: HTTP {status}")
        return None, {}

    auth_url = discovery.get("authorizationUrl", "")
    if not auth_url:
        print("  Discovery has no authorizationUrl")
        return None, discovery

    spaces = discovery.get("spaces", [])
    space_id = spaces[0].get("spaceId", "") if spaces else ""
    print(f"  Login URL: {auth_url}")
    print(f"  Space ID:  {space_id}")

    if not space_id:
        print("  No spaceId in discovery")
        return None, discovery

    # Step 2: Login with OIDC id_token
    login_body = json.dumps(
        {
            "data": {
                "type": "consumerLoginRequest",
                "attributes": {
                    "email": email,
                    "token": id_token,
                    "identityProvider": "DI",
                    "spaceId": space_id,
                },
            }
        }
    )

    status, data = api_request(
        auth_url,
        data=login_body,
        headers=jsonapi_headers,
        method="POST",
    )
    if status not in (200, 201) or not isinstance(data, dict):
        print(f"  Backend login failed: HTTP {status}")
        return None, discovery

    token = data.get("data", {}).get("attributes", {}).get("token")
    if not token:
        token = data.get("token")
    if token:
        print("  Backend login succeeded")
        return token, discovery

    print(f"  Backend login response has no token, keys: {list(data.keys())}")
    return None, discovery


def fetch_appliances_via_homeid(oidc_tokens, email):
    """Fetch appliances via the Home ID backend API (primary method).

    Chain: discovery -> backend login -> profile -> appliances.
    Returns list of appliance dicts with clientId/clientSecret.
    """
    access_token = oidc_tokens.get("access_token", "")
    ts = int(time.time() * 1000)

    backend_token, discovery = backend_login(oidc_tokens, email)

    auth_token = backend_token or access_token
    token_source = "backend" if backend_token else "oidc"
    print(f"  Using {token_source} token for Home ID API")

    if not discovery:
        print("  No discovery data available")
        return []

    hal_headers = {
        "Authorization": f"Bearer {auth_token}",
        "Accept": HOMEID_ACCEPT,
        "Accept-Language": "en-GB",
        "User-Agent": HOMEID_USER_AGENT,
        "X-USER-AGENT": HOMEID_X_USER_AGENT,
    }

    profile_url = discovery.get("profileUrl")
    if not profile_url:
        print(f"  No profileUrl in discovery, keys: {list(discovery.keys())}")
        return []

    if profile_url.startswith("/"):
        profile_url = f"{BACKEND_API_BASE}{profile_url}"

    # Step 3: Get user profile
    profile_req_url = f"{profile_url}?ts={ts}"
    print(f"  Profile: GET {profile_req_url}")
    status, profile = api_request(profile_req_url, headers=hal_headers)
    if status != 200 or not isinstance(profile, dict):
        print(f"  Profile failed: HTTP {status}")
        return []

    # Try embedded appliances first
    embedded = profile.get("_embedded", {})
    appliances_embedded = embedded.get("userAppliances", {})
    if isinstance(appliances_embedded, dict):
        items = appliances_embedded.get("_embedded", {}).get("item", [])
        if items:
            print(f"  Found {len(items)} embedded appliance(s) in profile")
            return items

    # Follow HAL link to appliances
    links = profile.get("_links", {})
    appliances_link = links.get("userAppliances", {})
    appliances_href = (
        appliances_link.get("href", "") if isinstance(appliances_link, dict) else ""
    )

    if not appliances_href:
        print(f"  No userAppliances link, _links keys: {list(links.keys())}")
        return []

    if appliances_href.startswith("/"):
        appliances_href = f"{BACKEND_API_BASE}{appliances_href}"

    # Expand HAL URI template: strip {?param} placeholders
    appliances_href = re.sub(r"\{[^}]*\}", "", appliances_href)

    # Step 4: Get appliances
    appliances_req_url = f"{appliances_href}?ts={ts}&includeSkippedPairing=true"
    print(f"  Appliances: GET {appliances_req_url}")
    status, appliances_data = api_request(appliances_req_url, headers=hal_headers)
    if status != 200:
        print(f"  Appliances failed: HTTP {status}")
        return []

    if isinstance(appliances_data, dict):
        items = appliances_data.get("_embedded", {}).get("item", [])
    elif isinstance(appliances_data, list):
        items = appliances_data
    else:
        items = []

    print(f"  Found {len(items)} appliance(s)")
    return items


# IoT API (fallback method)


def fetch_devices(access_token):
    """List devices from IoT API."""
    status, body = api_request(
        f"{IOT_BASE}/user/self/device",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    if status != 200:
        return []
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        return body.get("devices") or body.get("data") or body.get("items") or []
    return []


def fetch_device_migration(access_token, ctns):
    """Query device migration endpoint with CTN list."""
    ctn_params = "&".join(f"ctn={c}" for c in ctns)
    status, body = api_request(
        f"{IOT_BASE}/user/self/device-migration?{ctn_params}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    return status, body


def post_device_migration(access_token, device_ids, ctns):
    """POST device migration to generate local credentials."""
    ctn_params = "&".join(f"ctn={c}" for c in ctns)
    status, body = api_request(
        f"{IOT_BASE}/user/self/device-migration?{ctn_params}",
        data=json.dumps(
            {
                "sourceAppId": "com.philips.ka.oneka.app",
                "deviceIds": device_ids,
            }
        ),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    return status, body


def print_summary(homeid_appliances, iot_devices):
    """Print device credentials summary."""
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)

    if homeid_appliances:
        print("\n--- Home ID API (primary) ---")
        for item in homeid_appliances:
            name = item.get("name", "") or "Unknown"
            mac = item.get("macAddress", "")
            print(f"\nDevice: {name}")
            print(f"  MAC:            {mac or '?'}")
            print(f"  Firmware:       {item.get('firmwareVersion', '?')}")
            print(f"  External ID:    {item.get('externalDeviceId', '?')}")
            print(f"  Registered in:  {item.get('registeredIn', '?')}")

            client_id = item.get("clientId", "")
            client_secret = item.get("clientSecret", "")
            if client_id and client_secret:
                print(f"  client_id:      {client_id}")
                print(f"  client_secret:  {client_secret}")
            else:
                print("  Credentials:    not available from Home ID API")

    if iot_devices:
        print("\n--- IoT API (fallback) ---")
        for dev in iot_devices:
            name = dev.get("friendlyName") or dev.get("ctn", "Unknown")
            print(f"\nDevice: {name}")
            print(f"  Model (CTN):  {dev.get('ctn', '?')}")
            print(f"  MAC:          {dev.get('macAddress', '?')}")
            print(f"  Device ID:    {dev.get('id', '?')}")
            print(f"  Thing Name:   {dev.get('thingName', '?')}")

            creds = dev.get("localCredentials")
            if creds:
                print(f"  Local Creds:  {creds}")
                try:
                    parsed = json.loads(creds)
                    for k, v in parsed.items():
                        print(f"    {k}: {v}")
                except (json.JSONDecodeError, TypeError):
                    pass
            else:
                print("  Local Creds:  not available")

    if not homeid_appliances and not iot_devices:
        print("\nNo devices found in your Philips cloud account.")
        print("Devices must be registered via the HomeID app first.")


def fetch_credentials(email, oidc_tokens, access_token, iot_only=False):
    """Fetch device credentials using Home ID and IoT APIs."""
    # Try Home ID API first (primary method)
    homeid_appliances = []
    if not iot_only and oidc_tokens:
        print("\nQuerying Home ID API (primary)...")
        homeid_appliances = fetch_appliances_via_homeid(oidc_tokens, email)

    # Query IoT API (fallback or additional info)
    print("\nQuerying IoT API...")

    # User profile
    status, user_info = api_request(
        f"{IOT_BASE}/user/self",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    if status == 200 and isinstance(user_info, dict):
        print(f"  Cloud user ID: {user_info.get('id', '?')}")

    # Device list
    iot_devices = fetch_devices(access_token)
    print(f"  Devices found: {len(iot_devices)}")

    if iot_devices:
        # Try device migration for credentials
        ctns = list({d.get("ctn") for d in iot_devices if d.get("ctn")})
        device_ids = [d["id"] for d in iot_devices if d.get("id")]

        if ctns:
            print(f"\nQuerying device migration (CTNs: {ctns})...")
            status, mig = fetch_device_migration(access_token, ctns)
            if status == 200:
                print(f"  Migration response: {json.dumps(mig, indent=2)[:2000]}")
                if isinstance(mig, dict):
                    mig_devs = mig.get("devices", [])
                    for md in mig_devs:
                        for d in iot_devices:
                            if d.get("id") == md.get("id"):
                                d["localCredentials"] = md.get("localCredentials")

        if device_ids:
            print("\nAttempting credential migration (POST)...")
            status, mig = post_device_migration(access_token, device_ids, ctns)
            print(f"  HTTP {status}: {json.dumps(mig, indent=2)[:2000]}")

    # Homes
    status, homes = api_request(
        f"{IOT_BASE}/user/self/home",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    if status == 200 and isinstance(homes, list) and homes:
        print(f"  Homes: {json.dumps(homes, indent=2)[:500]}")

    # Test HSDP token chain for FUSION MQTT
    hsdp_at = oidc_tokens.get("hsdp_access_token", "") if oidc_tokens else ""
    hsdp_rt = oidc_tokens.get("hsdp_refresh_token", "") if oidc_tokens else ""
    if hsdp_at:
        print("\n--- HSDP Token Tests ---")
        print("\n  Testing MQTT signature with HSDP token...")
        test_mqtt_signature(hsdp_at)

        print("\n  Testing MQTT signature with Gigya token (comparison)...")
        test_mqtt_signature(access_token)

        if hsdp_rt:
            print("\n  Testing HSDP token refresh...")
            refreshed = refresh_hsdp_tokens(hsdp_rt)
            if refreshed:
                print("    HSDP token refresh succeeded!")
                print(f"    New expires_in: {refreshed.get('expires_in', '?')}")
    else:
        print("\n--- HSDP Token Tests ---")
        print("  No HSDP tokens available (SSO did not produce HSDP code)")
        print("  FUSION MQTT will fall back to Gigya tokens")
        print("\n  Testing MQTT signature with Gigya token...")
        test_mqtt_signature(access_token)

    # Token diagnostics
    print("\n--- Gigya OIDC Token Diagnostics ---")
    at_parts = access_token.split(".")
    if len(at_parts) >= 2:
        at_pad = at_parts[1] + "=" * (4 - len(at_parts[1]) % 4)
        at_claims = json.loads(base64.urlsafe_b64decode(at_pad))
        gigya_sub = at_claims.get("sub", "unknown")
        print(f"  sub: {gigya_sub}")
        print(f"  aud: {at_claims.get('aud', 'NONE')}")
        print(f"  iss: {str(at_claims.get('iss', ''))[:60]}")
        print(f"  client_id: {at_claims.get('client_id', 'NONE')}")
        print(f"  azp: {at_claims.get('azp', 'NONE')}")
        print(f"  scope: {str(at_claims.get('scope', ''))[:80]}")
    else:
        gigya_sub = "unknown"

    # Comprehensive MQTT connection tests
    print("\n--- MQTT Connection Tests (all combinations) ---")

    # Get Gigya-derived signature
    sig_status, sig_body = api_request(
        f"{IOT_BASE}/user/self/signature",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    gigya_sig = sig_body.get("signature", "") if isinstance(sig_body, dict) else ""
    print(f"  Gigya signature: {len(gigya_sig)} chars")

    # Test 1: APK-verified flow (Gigya OIDC token + Gigya signature)
    # APK uses: token-header=Gigya access_token, signature from /user/self/signature
    # Client ID: {gigya_sub}_{short_hex} (APK strips @fed-... via UserId value class)
    print(f"\n  [APK flow: Gigya OIDC token + Gigya sig, sub={gigya_sub[:20]}...]")
    test_mqtt_connection(access_token, custom_sig=gigya_sig)

    # Test 2: APK flow with REFRESHED Gigya token
    # The APK's AppAuth refreshes tokens via grant_type=refresh_token.
    # Refreshed tokens may have different aud claim than initial tokens.
    if oidc_tokens and oidc_tokens.get("refresh_token"):
        print("\n  Refreshing Gigya OIDC token...")
        ref_status, ref_body = api_request(
            "https://cdc.accounts.home.id/oidc/op/v1.0/4_JGZWlP8eQHpEqkvQElolbA/token",
            data={
                "client_id": "-u6aTznrxp9_9e_0a57CpvEG",
                "grant_type": "refresh_token",
                "refresh_token": oidc_tokens["refresh_token"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if ref_status == 200 and isinstance(ref_body, dict):
            ref_at = ref_body.get("access_token", "")
            # Decode and show refreshed token claims
            ref_parts = ref_at.split(".")
            if len(ref_parts) >= 2:
                ref_pad = ref_parts[1] + "=" * (4 - len(ref_parts[1]) % 4)
                ref_claims = json.loads(base64.urlsafe_b64decode(ref_pad))
                print(f"  Refreshed aud: {ref_claims.get('aud', 'NONE')}")
                print(f"  Refreshed sub: {ref_claims.get('sub', 'NONE')}")

            # Get fresh signature with refreshed token
            ref_sig_status, ref_sig_body = api_request(
                f"{IOT_BASE}/user/self/signature",
                headers={
                    "Authorization": f"Bearer {ref_at}",
                    "Accept": "application/json",
                },
            )
            ref_sig = (
                ref_sig_body.get("signature", "")
                if isinstance(ref_sig_body, dict)
                else ""
            )
            if ref_sig:
                print(f"  Refreshed signature: {len(ref_sig)} chars")
                print("\n  [APK flow REFRESHED: Gigya refreshed token + refreshed sig]")
                test_mqtt_connection(ref_at, custom_sig=ref_sig)
            else:
                print(f"  Refreshed signature failed: HTTP {ref_sig_status}")
        else:
            print(f"  Gigya refresh failed: HTTP {ref_status}")

    # SAS token exchange
    sas_at = ""
    sas_signed = ""
    sas_id = ""
    if oidc_tokens and oidc_tokens.get("id_token"):
        sas_body = json.dumps(
            {
                "idToken": oidc_tokens["id_token"],
                "exchangeFor": "HSDP",
            }
        )
        sas_status, sas_resp = api_request(
            "https://www.backend.vbs.versuni.com/api/TokenExchange",
            data=sas_body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.oneka.v2.0+json",
                "Content-Type": "application/vnd.oneka.v2.0+json",
            },
        )
        if sas_status == 200 and isinstance(sas_resp, dict):
            sas_at = sas_resp.get("accessToken", "")
            sas_signed = sas_resp.get("signedToken", "")
            sas_id = sas_resp.get("idToken", "")
            print(f"\n  SAS accessToken: {sas_at[:20]}... ({len(sas_at)} chars)")
            print(f"  SAS signedToken: {len(sas_signed)} chars")
            print(f"  SAS idToken: {len(sas_id)} chars")
        else:
            print(f"\n  SAS exchange failed: HTTP {sas_status}")

    # Test remaining combinations
    extra_tests = []
    if sas_at and gigya_sig:
        extra_tests.append(("SAS accessToken + Gigya sig", sas_at, gigya_sig))
    if sas_at and sas_signed:
        extra_tests.append(("SAS accessToken + signedToken", sas_at, sas_signed))
    if sas_id and gigya_sig:
        extra_tests.append(("SAS idToken + Gigya sig", sas_id, gigya_sig))
    if sas_id and sas_signed:
        extra_tests.append(("SAS idToken + signedToken", sas_id, sas_signed))

    for label, tok, sig in extra_tests:
        print(f"\n  [{label}]")
        test_mqtt_connection(tok, custom_sig=sig)

    print_summary(homeid_appliances, iot_devices)


def authenticate(email):
    """Run the full OTP + OAuth flow interactively. Returns (oidc_tokens, access_token)."""
    print(f"\nSending verification code to {email}...")
    vtoken = request_otp(email)
    print("  Verification code sent! Check your email.")

    code = input("\nEnter verification code: ").strip()
    if not code:
        print("No code entered.")
        return None, None

    print("\nVerifying code...")
    session_token = verify_otp(email, code, vtoken)
    print("  Login successful.")

    print("\nRunning headless OAuth flow (this may take a moment)...")
    oidc_tokens = headless_oauth(session_token, email=email)
    if not oidc_tokens:
        print("  Failed to obtain OIDC tokens.")
        return None, None

    access_token = oidc_tokens["access_token"]

    # Decode and show token info
    parts = oidc_tokens.get("id_token", "").split(".")
    if len(parts) >= 2:
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        print(f"  Authenticated as: {payload.get('sub', '?')[:30]}")

    # Save for resume
    with open(STATE_FILE, "w") as f:
        json.dump(oidc_tokens, f)
    print(f"  Tokens saved to {STATE_FILE}")

    return oidc_tokens, access_token


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Philips HomeID device credentials from cloud"
    )
    parser.add_argument("email", nargs="?", help="Philips HomeID account email")
    parser.add_argument("otp_code", nargs="?", help="OTP code (non-interactive mode)")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse saved OIDC tokens (valid ~1 hour)",
    )
    parser.add_argument(
        "--iot-only",
        action="store_true",
        help="Skip Home ID API, only use IoT API",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Maximum debug output (all HTTP requests, full HSDP SSO trace)",
    )
    args = parser.parse_args()

    global DEBUG
    DEBUG = args.debug

    print("Philips HomeID Cloud Key Fetcher")
    print("=" * 40)

    access_token = None
    oidc_tokens = None
    email = args.email

    # Resume with saved tokens
    if args.resume and os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            oidc_tokens = json.load(f)
        access_token = oidc_tokens.get("access_token")
        if access_token:
            print("Resuming with saved tokens.")
            if not email:
                email = input("Email (needed for Home ID API): ").strip()

    if not access_token:
        # Get email if not provided
        if not email:
            email = input("\nEnter your Philips HomeID email: ").strip()
            if not email:
                print("No email entered.")
                return

        if args.otp_code:
            # Non-interactive: OTP code provided on command line
            vtoken_file = "/tmp/philips_vtoken.txt"
            if not os.path.exists(vtoken_file):
                print("No vToken found. Run without code first to send OTP.")
                return
            with open(vtoken_file) as f:
                vtoken = f.read().strip()

            print("\nVerifying OTP...")
            session_token = verify_otp(email, args.otp_code, vtoken)
            print("  Login successful.")

            print("\nRunning headless OAuth flow...")
            oidc_tokens = headless_oauth(session_token, email=email)
            if not oidc_tokens:
                print("  Failed to obtain OIDC tokens.")
                return

            access_token = oidc_tokens["access_token"]

            parts = oidc_tokens.get("id_token", "").split(".")
            if len(parts) >= 2:
                padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
                payload = json.loads(base64.urlsafe_b64decode(padded))
                print(f"  Authenticated as: {payload.get('sub', '?')[:30]}")

            with open(STATE_FILE, "w") as f:
                json.dump(oidc_tokens, f)

            if os.path.exists(vtoken_file):
                os.remove(vtoken_file)
        else:
            # Interactive mode
            oidc_tokens, access_token = authenticate(email)
            if not access_token:
                return

    fetch_credentials(email, oidc_tokens, access_token, args.iot_only)


if __name__ == "__main__":
    main()
