<p align="center">
  <img src="images/logo.png" alt="Philips HomeID" width="120">
</p>

# Philips HomeID Integration for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/renaudallard/homeassistant_philips_homeid)
[![Release](https://img.shields.io/badge/release-v2.0.1-blue.svg)](https://github.com/renaudallard/homeassistant_philips_homeid/releases)
[![License](https://img.shields.io/badge/license-BSD--2--Clause-green.svg)](LICENSE)

Control your Philips domestic appliances locally through Home Assistant. No cloud dependency after initial setup.

---

## Supported Devices

| Category | Models | Architecture | Notes |
|----------|--------|--------------|-------|
| **Air Purifiers** | AC0650, AC0651, AC series | | Local network connectivity required |
| **Air Fryers** | HD9200, HD9255, HD9280, HD9285 | SPECTRE | Single basket, port `airfryer` |
| **Air Fryers** | HD9875, HD9876 | VENUS 1 | Single basket, port `venus1af` |
| **Air Fryers** | HD9880 | VENUS 2 | Single basket, port `venusaf` |
| **Multicookers** | NX0960 | NUTRIMAX | Port `nutrimax` |
| **Multicookers** | NX0950 | HERMES | Port `hermesac` |

> **Note:** Some devices report their internal codename (e.g., "Venus2", "Spectre") instead of the marketing model number (e.g., "HD9880"). The integration recognizes both.

> **Note:** Espresso machines (EP series) use cloud-based communication and are not supported.
>
> **Note:** Some newer firmware versions (e.g., HD9280, HD9285) have the app communicate exclusively via cloud relay and never store local credentials on the phone. The built-in cloud login handles these devices automatically by retrieving credentials from the Philips cloud. See [Cloud-only firmware](#cloud-only-firmware) in Troubleshooting for details.

---

## Features

| Feature | Description |
|---------|-------------|
| **Local Control** | Direct communication over your local network |
| **Auto Discovery** | Automatic device detection via Zeroconf/SSDP |
| **Smart Polling** | Configurable intervals (default 60s idle / 10s cooking) |
| **Extrapolated Timers** | Smooth countdown updates between polls |
| **Dynamic Entities** | Sensors created only when device reports data |
| **Firmware Updates** | Shows installed and available firmware versions |
| **Diagnostics** | Built-in diagnostics for troubleshooting |
| **Cloud Login** | Retrieve credentials from Philips cloud via your account |

### Air Purifiers
- Fan speed and preset modes (auto, manual, sleep, turbo, allergen, bacteria, night)
- Air quality sensors: PM1, PM2.5, PM10, TVOC, gas, allergen index
- Environment sensors: humidity, temperature
- Filter status: pre-filter, HEPA, carbon, humidifier wick
- Controls: power, child lock
- MUJI devices (AC0650/AC0651): beep volume, sensor monitor, air quality threshold, filter lifetime tracking

### Air Fryers
- Cooking status, temperature (target/current), time remaining
- Controls: start, pause, stop, keep warm, temperature, cook time, preheat toggle
- Cooking method select (architecture-specific presets)
- Sensors: drawer state, preheat status, shake/flip reminders
- Venus devices: air speed, probe temperature, dialog, voltage, previous status
- AutoCook program tracking (UUID, doneness, amount, weight, thickness)
- Recipe stage tracking
- Multiple device architectures supported (SPECTRE, VENUS 1, VENUS 2)

### Multicookers
- Same cooking controls as air fryers (start, pause, stop, keep warm)
- Sensors: humidity, ingredient, temperature, cooking status
- Binary sensors: lid open, no water
- Architecture-specific cooking presets (Nutrimax: 10 methods, Hermes: 14 methods)

---

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the menu (three dots) in the top right corner
3. Select **Custom repositories**
4. Add this repository:
   ```
   https://github.com/renaudallard/homeassistant_philips_homeid
   ```
5. Select **Integration** as the category
6. Click **Add**
7. Search for **Philips HomeID** and click **Download**
8. Restart Home Assistant

### Manual Installation

1. Download `philips_homeid.zip` from the latest [GitHub Release](https://github.com/renaudallard/homeassistant_philips_homeid/releases)
2. Extract and copy the `philips_homeid` folder to your Home Assistant's `custom_components` directory
3. Restart Home Assistant

---

## Configuration

### Prerequisites

Your device must first be paired with the **official Philips HomeID app** on Android or iOS. The app handles the initial device pairing (including WiFi setup). This integration retrieves the credentials needed for local API access either automatically from the Philips cloud or manually from the app's local storage.

### Adding a Device

1. Go to **Settings** > **Devices & Services** > **Add Integration**
2. Search for **Philips HomeID**
3. Enter the device's IP address (or accept an auto-discovered device)
4. Enter your Philips HomeID account email
5. Wait while required components are installed (first run only)
6. Enter the verification code sent to your email
6. Select your device from the list and credentials are retrieved automatically

If cloud login is not available on your platform, or you prefer to enter credentials manually, check **Enter credentials manually instead** on the email form. See [Extracting Credentials](#extracting-credentials-manual-alternative) for how to obtain them.

#### Cloud Login

After confirming the discovered device, the integration uses cloud login to retrieve credentials. The cloud login flow:

1. Authenticates with Philips via email OTP (one-time password)
2. Temporarily installs a headless Chromium browser (Playwright) to complete OAuth authentication
3. Queries the Philips Home ID backend API to retrieve your device's `client_id` and `client_secret`
4. Uninstalls Playwright after use (only if it was not already installed)

> **Platform requirements:** Cloud login uses Playwright (headless Chromium) for the OAuth step. Supported platforms:
> - Linux x86_64 (Intel/AMD 64-bit)
> - Linux aarch64 (ARM 64-bit, e.g., Raspberry Pi 4/5 with 64-bit OS)
> - macOS (Intel and Apple Silicon)
> - Windows (x86, x64, ARM64)
>
> **Not supported:** Linux armv7 (32-bit ARM, e.g., Raspberry Pi with 32-bit OS), Alpine Linux (musl libc). On unsupported platforms, the integration will show an error and you should use the credential extractor tool or enter credentials manually.

To debug cloud login issues, enable debug logging:
```yaml
logger:
  default: warning
  logs:
    custom_components.philips_homeid.cloud_api: debug
    custom_components.philips_homeid.config_flow: debug
```

### Auto-Discovered Devices

Devices discovered via Zeroconf or SSDP will appear automatically as a notification. Clicking the notification starts the setup flow with cloud login.

---

## Extracting Credentials (Manual Alternative)

If cloud login is not available on your platform or does not work for your device, you can extract credentials manually from the official Philips HomeID app. The app package name is `com.philips.ka.oneka.app`.

### Method 1: Credential Extractor Tool

The included credential extractor tool automatically tries all known storage locations — SQLite database, EncryptedSharedPreferences, SecurePreferences, and AES-CBC fallback preferences. It works on all firmware versions and handles the Tink/Android Keystore decryption transparently.

<details>
<summary><b>Step-by-step instructions</b></summary>

1. **Install Android x86 in a VM** with root access (VirtualBox or VMware)
   - The VM must be on the same network as your Philips device

2. **Set up the environment**
   - Install Philips HomeID app from Play Store
   - Update Chrome (required for authentication)
   - Log into your Philips account

3. **Download and push the credential extractor**
   - Download `extractor.dex` and `extract_creds.sh` from the [`tools/credential_extractor/`](https://github.com/renaudallard/homeassistant_philips_homeid/tree/main/tools/credential_extractor) directory
   - Push them to the device:
     ```sh
     adb push extractor.dex /data/local/tmp/
     adb push extract_creds.sh /data/local/tmp/
     ```
   - Run as root:
     ```sh
     adb shell
     su
     sh /data/local/tmp/extract_creds.sh
     ```
   - On newer firmwares where the database is empty, pass the device MAC address (from the app or device label):
     ```sh
     sh /data/local/tmp/extract_creds.sh e4:bc:96:00:00:00
     ```
   - If the extractor reports entries exist but finds no credentials, use `--dump-all` to see what the app actually stores:
     ```sh
     sh /data/local/tmp/extract_creds.sh --dump-all e4:bc:96:00:00:00
     ```
   - Look for `client_id` / `DEVICE_CLIENT_ID` and `client_secret` / `DEVICE_CLIENT_SECRET` in the output
   - Note: `encryption_key` is only needed for HTTP devices (e.g., HD9285). If left empty, the integration will try to fetch it automatically from the device.

4. **If the extractor found no credentials**, your firmware may require an extra step. On some firmwares, the app initially communicates with the device via Philips cloud servers only and does not store local credentials until you explicitly trigger local authentication:
   - Make sure the VM is on the **same network** as your Philips device
   - Open the app and look for the **"Your appliance needs updating"** banner on the home screen or device dashboard
   - Tap **"Ok, let's start"** to trigger local authentication
   - This generates `client_id` and `client_secret` and stores them on the VM
   - Run the credential extractor again (step 3)

See [tools/credential_extractor/README.md](tools/credential_extractor/README.md) for full details and troubleshooting.

</details>

### Method 2: Cloud Key Fetcher (Standalone Tool)

A standalone command-line version of the cloud login is available for debugging or use outside Home Assistant. This is the same authentication flow used by the built-in cloud login.

<details>
<summary><b>Step-by-step instructions</b></summary>

1. **Install dependencies**
   ```sh
   pip install playwright && playwright install chromium
   ```

2. **Run the tool**
   ```sh
   python3 tools/cloud_key_fetcher.py your@email.com          # sends OTP to your email
   python3 tools/cloud_key_fetcher.py your@email.com 123456    # verify OTP + fetch devices
   ```

3. **Check results** - the tool will print any registered devices and their credentials if available.

See [`tools/cloud_key_fetcher.py`](tools/cloud_key_fetcher.py) for details.

</details>

### Method 3: SQLite Database (manual alternative)

On older firmwares, the app stores credentials in an unencrypted SQLite database. You can read them manually if you prefer not to use the extractor tool.

<details>
<summary><b>Step-by-step instructions</b></summary>

1. **Install Android x86 in a VM** (VirtualBox or VMware)
   - The VM must be on the same network as your Philips device

2. **Set up the environment**
   - Install Philips HomeID app from Play Store
   - Update Chrome (required for authentication)
   - Pair your device with the app

3. **Install SQLite Database Editor**
   - [SQLite Database Editor](https://play.google.com/store/apps/details?id=com.tomminosoftware.sqliteeditor)

4. **Extract credentials**
   - Open SQLite Database Editor (grant root access)
   - Navigate to: `com.philips.ka.oneka.app` > `network_node.db` > `network_node`
   - Find `client_id`, `client_secret`, and `encryption_key` columns
   - Enter these values during integration setup
   - Note: if the database is empty, your firmware stores credentials in encrypted preferences — use Method 1 instead.

</details>

---

## Entities

<details>
<summary><b>Air Purifier Entities</b></summary>

| Type | Entity | Description |
|------|--------|-------------|
| Fan | Air Purifier | Main control with speed and preset modes |
| Sensor | PM1.0 / PM2.5 / PM10 | Particulate matter readings |
| Sensor | Air Quality Index | Indoor air quality (IAQL) |
| Sensor | Total VOC | Volatile organic compounds |
| Sensor | Gas Level | Gas/formaldehyde level |
| Sensor | Allergen Index | Allergen level indicator |
| Sensor | Humidity / Temperature | Environmental readings |
| Sensor | Pre-filter / HEPA / Carbon | Filter remaining life |
| Sensor | Display Brightness | Current brightness level |
| Sensor | Total Runtime | Device runtime in hours |
| Sensor | Mode / Fan Speed | Current settings |
| Sensor | Error Code | Device error status |
| Sensor | Firmware Version | Installed firmware version |
| Sensor | Firmware Available | Available firmware upgrade |
| Binary Sensor | Filter Replace Required | Filter needs replacement |
| Binary Sensor | Water Tank Empty | Water tank status |
| Switch | Child Lock | Child lock control |
| Switch | Sensor Monitor in Standby | Keep sensors active in standby (MUJI only) |
| Number | Beep Volume / Air Quality Threshold | Device settings (MUJI only) |
| Sensor | Filter 0/1 Lifetime / Remaining | Filter tracking (MUJI only) |

</details>

<details>
<summary><b>Air Fryer Entities</b></summary>

| Type | Entity | Description |
|------|--------|-------------|
| Sensor | Cooking Status | Current cooking state |
| Sensor | Target / Current Temperature | Temperature readings |
| Sensor | Total Cook Time / Time Remaining | Timing information |
| Sensor | Preset / Recipe | Selected program |
| Sensor | Preheat Status / Keep Warm | Cooking modes |
| Sensor | Error Code | Device error status |
| Sensor | Firmware Version | Installed firmware version |
| Sensor | Firmware Available | Available firmware upgrade |
| Sensor | Recipe ID / Step ID | Cooking program identifiers (diagnostic) |
| Sensor | Air Speed | Fan speed setting (Venus only) |
| Sensor | Probe Temperature | Probe temperature reading (Venus only) |
| Sensor | Dialog | Device dialog/notification (Venus only) |
| Sensor | Previous Status | Previous cooking state (Venus only) |
| Sensor | Cooking ID / Current Stage | Cooking session details (Venus only) |
| Sensor | Voltage | Device voltage (Venus only, diagnostic) |
| Binary Sensor | Drawer | Drawer open/closed |
| Binary Sensor | Shake / Flip Reminder | Food reminders |
| Binary Sensor | Preheat Active | Preheat cycle status |
| Binary Sensor | Probe Unplugged / Required | Probe connection state (Venus only) |
| Binary Sensor | Resting | Resting phase active (Venus only) |
| Button | Start / Pause / Stop | Cooking controls |
| Button | Keep Warm | Start keep warm mode (1 hour default) |
| Number | Set Temperature / Cook Time | Adjustable settings |
| Number | Set Air Speed | 0=LOW, 1=HIGH (Venus only) |
| Number | Set Probe Temperature | Target probe temp (Venus only) |
| Number | Keep Warm Duration / Temperature | Keep warm settings |
| Select | Cooking Method | Preset selection (architecture-specific) |
| Switch | Preheat | Enable preheat for next cooking start |
| Sensor | Current Probe Temperature | Live probe reading (Venus only) |
| Sensor | AutoCook Program / Doneness | AutoCook state (Venus only) |
| Sensor | AutoCook Amount / Weight / Thickness | AutoCook parameters (Venus only) |
| Sensor | Recipe Current Stage | Multi-stage recipe tracking (Venus only) |
| Update | Firmware | Installed and available firmware version |

</details>

<details>
<summary><b>Multicooker Entities (NX0960/NX0950)</b></summary>

All air fryer entities above (including target/current temperature and total cook time), plus:

| Type | Entity | Description |
|------|--------|-------------|
| Sensor | Humidity | Cooking chamber humidity |
| Sensor | Ingredient | Selected ingredient |
| Binary Sensor | Lid | Lid open/closed |
| Binary Sensor | No Water | Water tank empty |

</details>

> **Note:** Entities are automatically filtered by device type. Only relevant sensors appear for your specific model.

---

## Troubleshooting

### Device Not Found
- Ensure the device is on the same network as Home Assistant
- Verify the IP address is correct
- Check that the device is powered on and connected
- If the device was recently updated via the HomeID app, autodiscovery may not work if the firmware changed the mDNS service type. Try adding the device manually by IP address instead.
- Some devices (e.g., HD9285) use HTTP on port 80 instead of HTTPS on port 443. The integration will automatically try both protocols when probing.

### Cloud Login Not Available
If cloud login is not supported on your platform (see [platform requirements](#cloud-login)), check **Enter credentials manually instead** on the email form and enter credentials extracted from the app (see [Extracting Credentials](#extracting-credentials-manual-alternative)).

### No Credentials Found (Credential Extractor Returns Empty)
On some firmwares, the Philips app initially communicates with the device via **cloud relay** (Philips MQTT servers) and does not store local credentials. This can happen when you install the app on a new device and log into your Philips account without completing the local authentication step.

To generate local credentials, make sure the app and device are on the **same network**, then look for the **"Your appliance needs updating"** banner on the home screen or device dashboard. Tap **"Ok, let's start"** to trigger local authentication, then run the credential extractor again. See step 4 in [Method 1](#method-1-credential-extractor-tool).

### "Could not obtain encryption key" or "credentials invalid" with HTTP Toolkit credentials
HTTP devices (e.g., HD9285) require an `encryption_key` in addition to `client_id` and `client_secret`. When you enter credentials without an encryption key, the integration tries to fetch it from the device automatically. If that fails, the most common causes are:

- **Cloud vs local credentials**: HTTP Toolkit captures all traffic. Make sure the credentials you captured came from requests to the **device's local IP address**, not to Philips cloud servers. Cloud credentials will not work for local control.
- **Device not in correct state**: The encryption key exchange requires valid local credentials. If the device doesn't recognize the credentials, the exchange will fail.
- **Credential extractor is the recommended method**: The extractor reads credentials (including the encryption key) directly from the app's storage, which is more reliable than intercepting traffic. The extractor now automatically handles SELinux by temporarily setting it to Permissive when needed.

### Cloud-only Firmware

Some newer firmware versions do not generate local credentials at all. The Philips app communicates with the device exclusively through cloud relay (MQTT via `backend.vbs.versuni.com`), and the credential extractor finds nothing because there are no local credentials stored on the device.

**Known affected firmwares:**
- HD9280 firmware 4.0.0/0.6.8
- HD9285 firmware 1.6.2/0.6.8

**Symptoms:**
- The credential extractor finds no credentials in any method
- The `--dump-all` flag shows only app settings (no device credentials)
- `COMMUNICATION_LIB_PREFERENCES` (Method 2) is completely empty
- Traffic interception shows all communication goes through cloud websockets, never to the device's local IP
- The device is already paired with the app via the cloud
- Credentials captured from cloud traffic (HTTP Toolkit) do not work for local auth

**What is happening:** The device does run a local HTTP server (it is discoverable via zeroconf) and should support local control. However, the app chooses to use cloud-only communication on these firmwares. Since the app never performs local authentication, no `client_id` or `client_secret` are stored on the phone. The credential extractor tool will find nothing.

**Workaround:** The built-in **Cloud Login** retrieves credentials directly from the Philips cloud API using your account. This is now the default authentication method and handles cloud-only firmwares automatically. See [Cloud Login](#cloud-login) above.

### Empty Database
If `network_node.db` is empty in the SQLite editor, your device firmware stores credentials in EncryptedSharedPreferences instead of SQLite. Use [Method 1: Credential Extractor Tool](#method-1-credential-extractor-tool) or the built-in cloud login, which handles all storage locations including encrypted preferences.

---

## Technical Details

| Aspect | Details |
|--------|---------|
| Protocol | HTTPS (port 443) or HTTP (port 80) depending on device model/firmware |
| Authentication | PHILIPS-Condor challenge-response (SHA256) |
| Payload Encryption | AES-128-CBC/PKCS7 for HTTP devices (key fetched from `/security` endpoint) |
| Discovery | Zeroconf (`_philipscondor._tcp.local.` or `_http._tcp.local.`) / SSDP (`urn:philips-com:device:DiProduct:1`) |
| Polling | Configurable via integration options (default: 60s idle, 10s cooking) |
| Port Discovery | Automatic: tries `airfryer`, `venusaf`, `venus1af`, `nutrimax`, `hermesac` |

---

## Disclaimer

This is an unofficial integration and is not affiliated with Philips or Versuni. Use at your own risk.

---

## License

BSD 2-Clause License - see [LICENSE](LICENSE) for details.

Copyright (c) 2025, Renaud Allard <renaud@allard.it>
