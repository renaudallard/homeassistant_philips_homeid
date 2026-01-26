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
Philips Airfryer Wi-Fi Provisioning Script

Use this script after factory resetting your airfryer to:
1. Send your home Wi-Fi credentials to the device
2. Pair with the device

Instructions:
1. Factory reset your airfryer (hold power button 10+ seconds or see manual)
2. The airfryer will create a Wi-Fi network like "PHILIPS-Airfryer-XXXX"
3. Connect your computer to that Wi-Fi network
4. Run this script: python provision_airfryer.py
5. After the script completes, the airfryer will connect to your home Wi-Fi
6. Reconnect your computer to your home Wi-Fi
7. Add the device in Home Assistant

The script will output the client_id and client_secret - save these!
You can enter them manually in Home Assistant if auto-discovery doesn't work.
"""

import argparse
import base64
import hashlib
import json
import secrets
import ssl
import sys
import urllib.request
import urllib.error

# Default device IP when in AP mode
DEFAULT_DEVICE_IP = "192.168.1.1"


def create_ssl_context():
    """Create SSL context that doesn't verify certificates."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_request(url: str, method: str = "GET", data: dict = None) -> tuple[int, dict]:
    """Make HTTP request and return (status_code, response_json)."""
    ctx = create_ssl_context()

    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode() if data else None

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            text = resp.read().decode()
            try:
                return resp.status, json.loads(text) if text else {}
            except json.JSONDecodeError:
                return resp.status, {"raw": text}
    except urllib.error.HTTPError as e:
        text = e.read().decode() if e.fp else ""
        try:
            return e.code, json.loads(text) if text else {}
        except json.JSONDecodeError:
            return e.code, {"raw": text}
    except Exception as e:
        return 0, {"error": str(e)}


def send_wifi_credentials(device_ip: str, ssid: str, password: str) -> bool:
    """Send Wi-Fi credentials to the device."""
    url = f"https://{device_ip}/di/v1/products/0/wifi"
    data = {"ssid": ssid, "password": password}

    print(f"Sending Wi-Fi credentials to {url}...")
    status, resp = http_request(url, "PUT", data)

    if status == 200:
        print("Wi-Fi credentials sent successfully!")
        print("The device will now connect to your home network.")
        return True
    else:
        print(f"Failed to send Wi-Fi credentials: {status} {resp}")
        return False


def pair_device(device_ip: str) -> tuple[str, str] | None:
    """Pair with the device and return (client_id, client_secret)."""
    url = f"https://{device_ip}/auth/v1/"

    # Generate client_id
    client_id = base64.b64encode(secrets.token_bytes(16)).decode()

    print(f"Attempting to pair with device at {device_ip}...")
    print(f"Client ID: {client_id}")

    # Step 1: Send client_id
    data = {"id": client_id}
    status, resp = http_request(url, "PUT", data)

    print(f"Step 1 response ({status}): {resp}")

    if status != 200:
        print(f"Pairing failed at step 1: {status}")
        return None

    authenticated = resp.get("authenticated", False)
    seed = resp.get("seed")
    secret = resp.get("secret")

    # If already authenticated with secret, we're done
    if authenticated and secret:
        print("Pairing successful!")
        return client_id, secret

    if not seed:
        print("Device did not return a seed - may not be in pairing mode")
        return None

    # Step 2: Compute evidence and send back
    print("Computing evidence from seed...")

    try:
        seed_bytes = base64.b64decode(seed)
        client_id_bytes = base64.b64decode(client_id)

        # Try different evidence formats

        # Format 1: Just hash
        hash_result = hashlib.sha256(seed_bytes + client_id_bytes).digest()
        evidence1 = base64.b64encode(hash_result).decode()

        # Format 2: client_id + hash (like PHILIPS-Condor scheme)
        evidence2 = base64.b64encode(client_id_bytes + hash_result).decode()

    except Exception as e:
        print(f"Failed to compute evidence: {e}")
        return None

    # Try with "key" field
    for evidence in [evidence1, evidence2]:
        data = {"id": client_id, "key": evidence}
        status, resp = http_request(url, "PUT", data)
        print(f"Step 2 response with key ({status}): {resp}")

        if status == 200:
            authenticated = resp.get("authenticated", False)
            secret = resp.get("secret")

            if authenticated:
                print("Pairing successful!")
                return client_id, secret if secret else evidence

    # Try with "secret" field
    for evidence in [evidence1, evidence2]:
        data = {"id": client_id, "secret": evidence}
        status, resp = http_request(url, "PUT", data)
        print(f"Step 2 response with secret ({status}): {resp}")

        if status == 200:
            authenticated = resp.get("authenticated", False)
            secret = resp.get("secret")

            if authenticated:
                print("Pairing successful!")
                return client_id, secret if secret else evidence

    print("Pairing failed - device may already be paired with another app")
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Provision Philips Airfryer with Wi-Fi credentials and pair",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--ip",
        default=DEFAULT_DEVICE_IP,
        help=f"Device IP address (default: {DEFAULT_DEVICE_IP})"
    )
    parser.add_argument(
        "--ssid",
        help="Your home Wi-Fi network name (SSID)"
    )
    parser.add_argument(
        "--password",
        help="Your home Wi-Fi password"
    )
    parser.add_argument(
        "--pair-only",
        action="store_true",
        help="Only attempt pairing (skip Wi-Fi provisioning)"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("Philips Airfryer Provisioning Tool")
    print("=" * 60)
    print()

    if not args.pair_only:
        # Get Wi-Fi credentials
        ssid = args.ssid
        password = args.password

        if not ssid:
            ssid = input("Enter your home Wi-Fi SSID: ").strip()
        if not password:
            password = input("Enter your home Wi-Fi password: ").strip()

        if not ssid:
            print("Error: SSID is required")
            sys.exit(1)

        # Send Wi-Fi credentials
        print()
        if not send_wifi_credentials(args.ip, ssid, password):
            print()
            print("Failed to send Wi-Fi credentials.")
            print("Make sure you're connected to the airfryer's Wi-Fi network.")
            sys.exit(1)

        print()
        print("Wi-Fi credentials sent!")
        print()

    # Attempt pairing
    result = pair_device(args.ip)

    if result:
        client_id, client_secret = result
        print()
        print("=" * 60)
        print("SUCCESS! Save these credentials:")
        print("=" * 60)
        print(f"Client ID:     {client_id}")
        print(f"Client Secret: {client_secret}")
        print("=" * 60)
        print()
        print("You can now:")
        print("1. Reconnect to your home Wi-Fi")
        print("2. Add the airfryer in Home Assistant")
        print("3. If auto-discovery doesn't work, enter credentials manually")
    else:
        print()
        print("Pairing failed.")
        if not args.pair_only:
            print("The device may need a moment to process.")
            print("Try running with --pair-only after reconnecting to home Wi-Fi.")
        sys.exit(1)


if __name__ == "__main__":
    main()
