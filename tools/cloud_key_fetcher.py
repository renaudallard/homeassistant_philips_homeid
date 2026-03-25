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

    ctx = ssl.create_default_context()
    try:
        resp = urllib.request.urlopen(req, timeout=30, context=ctx)
        body = resp.read().decode()
        try:
            return resp.status, json.loads(body)
        except json.JSONDecodeError:
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode()
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


def headless_oauth(session_token):
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

        browser.close()

    if not auth_code:
        return None

    # Exchange code for tokens
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
        print(f"  Token exchange failed: {body}")
        return None

    return body


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
    oidc_tokens = headless_oauth(session_token)
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
    args = parser.parse_args()

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
            oidc_tokens = headless_oauth(session_token)
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
