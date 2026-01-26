#!/usr/bin/env python3
"""
Standalone Philips Airfryer Pairing Script

This script pairs with a Philips Airfryer and retrieves the credentials
needed for Home Assistant integration.

Usage:
1. Factory reset your airfryer (hold power button until it beeps)
2. The airfryer will create a Wi-Fi network like "PHILIPS-Airfryer-XXXX"
3. Connect your laptop to that network
4. Run this script: python3 pair_airfryer.py --wifi-ssid "YourHomeWiFi" --wifi-password "YourPassword"
5. The script will output client_id and client_secret
6. Enter these credentials in Home Assistant

Requirements:
    pip install aiohttp
"""

import argparse
import asyncio
import base64
import hashlib
import json
import secrets
import ssl
import sys
from typing import Any

import aiohttp

# Default device IP when connected to its AP
DEFAULT_DEVICE_IP = "192.168.1.1"


def create_ssl_context():
    """Create SSL context that accepts self-signed certificates."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def pair_device(session: aiohttp.ClientSession, device_ip: str) -> tuple[str | None, str | None]:
    """
    Pair with the device and get credentials.

    Returns: (client_id, client_secret) or (None, None) on failure
    """
    # Generate a random client_id (16 bytes, base64 encoded)
    random_bytes = secrets.token_bytes(16)
    client_id = base64.b64encode(random_bytes).decode("utf-8")

    url = f"https://{device_ip}/auth/v1/"

    print(f"[*] Starting pairing with device at {device_ip}")
    print(f"[*] Generated client_id: {client_id}")

    try:
        # Step 1: Initial pairing request
        data = {"id": client_id}
        print(f"[*] Step 1: Sending initial pairing request...")

        async with session.put(url, json=data) as resp:
            print(f"    Response status: {resp.status}")

            if resp.status == 200:
                result = await resp.json()
                print(f"    Response: {json.dumps(result, indent=2)}")

                # Check if we got a secret directly
                if "secret" in result:
                    client_secret = result["secret"]
                    print(f"\n[+] SUCCESS! Got credentials directly:")
                    print(f"    client_id: {client_id}")
                    print(f"    client_secret: {client_secret}")
                    return client_id, client_secret

                # Check if we need to compute evidence
                if "seed" in result:
                    seed = result["seed"]
                    print(f"    Got seed: {seed}")

                    # Compute evidence: base64(client_id_bytes + SHA256(seed_bytes + client_id_bytes))
                    seed_bytes = base64.b64decode(seed)
                    client_id_bytes = base64.b64decode(client_id)

                    hash_input = seed_bytes + client_id_bytes
                    hash_result = hashlib.sha256(hash_input).digest()

                    evidence_bytes = client_id_bytes + hash_result
                    computed_evidence = base64.b64encode(evidence_bytes).decode("utf-8")

                    print(f"[*] Step 2: Sending computed evidence...")

                    # Step 2: Send computed evidence
                    data2 = {"id": client_id, "key": computed_evidence}

                    async with session.put(url, json=data2) as resp2:
                        print(f"    Response status: {resp2.status}")

                        if resp2.status == 200:
                            result2 = await resp2.json()
                            print(f"    Response: {json.dumps(result2, indent=2)}")

                            if "secret" in result2:
                                client_secret = result2["secret"]
                                print(f"\n[+] SUCCESS! Got credentials:")
                                print(f"    client_id: {client_id}")
                                print(f"    client_secret: {client_secret}")
                                return client_id, client_secret
                            else:
                                # Use computed evidence as secret
                                print(f"\n[+] SUCCESS! Using computed evidence as secret:")
                                print(f"    client_id: {client_id}")
                                print(f"    client_secret: {computed_evidence}")
                                return client_id, computed_evidence
                        else:
                            text = await resp2.text()
                            print(f"    Error: {text}")

            elif resp.status == 401:
                # Device might need pairing mode
                print("    Device returned 401 - make sure it's in pairing mode")
                print("    (Factory reset: hold power button until it beeps)")
            else:
                text = await resp.text()
                print(f"    Error response: {text}")

    except aiohttp.ClientError as e:
        print(f"[-] Connection error: {e}")
    except Exception as e:
        print(f"[-] Error: {e}")

    return None, None


async def send_wifi_credentials(
    session: aiohttp.ClientSession,
    device_ip: str,
    wifi_ssid: str,
    wifi_password: str,
) -> bool:
    """Send home Wi-Fi credentials to the device."""
    url = f"https://{device_ip}/config/v1/wifiClientCredentials"

    data = {
        "ssid": wifi_ssid,
        "password": wifi_password,
    }

    print(f"[*] Sending Wi-Fi credentials for network: {wifi_ssid}")

    try:
        async with session.put(url, json=data) as resp:
            print(f"    Response status: {resp.status}")

            if resp.status in (200, 204):
                print("[+] Wi-Fi credentials sent successfully!")
                print("    The device should now connect to your home network.")
                return True
            else:
                text = await resp.text()
                print(f"    Error: {text}")

    except aiohttp.ClientError as e:
        print(f"[-] Connection error: {e}")
    except Exception as e:
        print(f"[-] Error: {e}")

    return False


async def get_device_info(session: aiohttp.ClientSession, device_ip: str) -> dict[str, Any] | None:
    """Get device information."""
    url = f"https://{device_ip}/di/v1/products/0/device"

    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception:
        pass

    return None


async def main(args: argparse.Namespace) -> int:
    """Main function."""
    print("=" * 60)
    print("Philips Airfryer Pairing Script")
    print("=" * 60)
    print()

    # Create SSL context that accepts self-signed certs
    ssl_ctx = create_ssl_context()
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    async with aiohttp.ClientSession(connector=connector) as session:
        # Try to get device info first
        print(f"[*] Checking connection to device at {args.device_ip}...")
        info = await get_device_info(session, args.device_ip)

        if info:
            print(f"[+] Connected to device!")
            print(f"    Device info: {json.dumps(info, indent=2)}")
        else:
            print("[!] Could not get device info (this may be normal)")

        print()

        # Pair with device
        client_id, client_secret = await pair_device(session, args.device_ip)

        if not client_id or not client_secret:
            print("\n[-] Pairing failed!")
            print("    Make sure:")
            print("    1. Your laptop is connected to the airfryer's Wi-Fi (PHILIPS-Airfryer-XXX)")
            print("    2. The airfryer is in pairing mode (factory reset if needed)")
            return 1

        print()

        # Send Wi-Fi credentials if provided
        if args.wifi_ssid:
            success = await send_wifi_credentials(
                session,
                args.device_ip,
                args.wifi_ssid,
                args.wifi_password or "",
            )

            if not success:
                print("\n[!] Warning: Failed to send Wi-Fi credentials")
                print("    You may need to configure Wi-Fi manually via the app")

        # Output summary
        print()
        print("=" * 60)
        print("CREDENTIALS FOR HOME ASSISTANT")
        print("=" * 60)
        print()
        print("Add these to your Home Assistant configuration:")
        print()
        print(f"  client_id: {client_id}")
        print(f"  client_secret: {client_secret}")
        print()
        print("After the device connects to your home network, find its")
        print("new IP address (check your router's DHCP list) and use it")
        print("when adding the device in Home Assistant.")
        print()

        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pair with a Philips Airfryer and get credentials",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Just pair (don't send Wi-Fi credentials)
  python3 pair_airfryer.py

  # Pair and send Wi-Fi credentials
  python3 pair_airfryer.py --wifi-ssid "MyHomeNetwork" --wifi-password "MyPassword"

  # Use different device IP
  python3 pair_airfryer.py --device-ip 192.168.4.1

Steps:
  1. Factory reset your airfryer (hold power button ~5 seconds until it beeps)
  2. Wait for the airfryer to create its Wi-Fi network (PHILIPS-Airfryer-XXXX)
  3. Connect your laptop to that Wi-Fi network
  4. Run this script
  5. Note down the client_id and client_secret
  6. Use these credentials when setting up Home Assistant
        """,
    )

    parser.add_argument(
        "--device-ip",
        default=DEFAULT_DEVICE_IP,
        help=f"Device IP address (default: {DEFAULT_DEVICE_IP})",
    )
    parser.add_argument(
        "--wifi-ssid",
        help="Your home Wi-Fi network name (SSID)",
    )
    parser.add_argument(
        "--wifi-password",
        default="",
        help="Your home Wi-Fi password",
    )

    args = parser.parse_args()

    try:
        sys.exit(asyncio.run(main(args)))
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user")
        sys.exit(130)
