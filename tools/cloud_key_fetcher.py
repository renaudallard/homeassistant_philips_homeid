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
IoT API for registered devices and their local credentials.

Requirements:
  pip install playwright && playwright install chromium

Usage:
  python3 cloud_key_fetcher.py <email>           # Request OTP
  python3 cloud_key_fetcher.py <email> <code>     # Verify OTP + fetch
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import ssl
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
    return body["vToken"]


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


def fetch_devices(access_token):
    """List devices from IoT API."""
    status, body = api_request(
        f"{IOT_BASE}/user/self/device",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    if status == 200 and isinstance(body, list):
        return body
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


def print_summary(devices):
    """Print device credentials summary."""
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)

    if not devices:
        print("\nNo devices found in your Philips cloud account.")
        print("Devices must be registered via the HomeID app first.")
        return

    for dev in devices:
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


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Philips HomeID device credentials from cloud"
    )
    parser.add_argument("email", help="Philips HomeID account email")
    parser.add_argument("otp_code", nargs="?", help="OTP code from email")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse saved OIDC tokens (valid ~1 hour)",
    )
    args = parser.parse_args()

    print("Philips HomeID Cloud Key Fetcher")
    print("=" * 40)

    access_token = None

    # Resume with saved tokens
    if args.resume and os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
        access_token = state.get("access_token")
        if access_token:
            print("Resuming with saved tokens.")

    if not access_token:
        vtoken_file = "/tmp/philips_vtoken.txt"

        if not args.otp_code:
            # Step 1: Request OTP
            print(f"\nSending OTP to {args.email}...")
            vtoken = request_otp(args.email)
            with open(vtoken_file, "w") as f:
                f.write(vtoken)
            print("OTP sent! Run again with the code:")
            print(f"  python3 {__file__} {args.email} <CODE>")
            return

        # Step 2: Verify OTP
        if not os.path.exists(vtoken_file):
            print("No vToken found. Run without code first.")
            return

        with open(vtoken_file) as f:
            vtoken = f.read().strip()

        print("\nVerifying OTP...")
        session_token = verify_otp(args.email, args.otp_code, vtoken)
        print("  Login successful.")

        # Step 3: Headless browser OAuth
        print("\nRunning headless OAuth flow...")
        oidc_tokens = headless_oauth(session_token)
        if not oidc_tokens:
            print("  Failed to obtain OIDC tokens.")
            return

        access_token = oidc_tokens["access_token"]

        # Decode and show token info
        parts = oidc_tokens.get("id_token", "").split(".")
        if len(parts) >= 2:
            padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded))
            print(f"  Authenticated as: {payload.get('sub', '?')[:30]}")
            print(f"  Token audience:   {payload.get('aud')}")

        # Save for resume
        with open(STATE_FILE, "w") as f:
            json.dump({"access_token": access_token}, f)
        print(f"  Tokens saved to {STATE_FILE}")

        # Clean up
        if os.path.exists(vtoken_file):
            os.remove(vtoken_file)

    # Step 4: Query IoT API
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
    devices = fetch_devices(access_token)
    print(f"  Devices found: {len(devices)}")

    if devices:
        # Try device migration for credentials
        ctns = list({d.get("ctn") for d in devices if d.get("ctn")})
        device_ids = [d["id"] for d in devices if d.get("id")]

        if ctns:
            print(f"\nQuerying device migration (CTNs: {ctns})...")
            status, mig = fetch_device_migration(access_token, ctns)
            if status == 200:
                print(f"  Migration response: {json.dumps(mig, indent=2)[:2000]}")
                # Merge localCredentials into device list
                if isinstance(mig, dict):
                    mig_devs = mig.get("devices", [])
                    for md in mig_devs:
                        for d in devices:
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

    print_summary(devices)


if __name__ == "__main__":
    main()
