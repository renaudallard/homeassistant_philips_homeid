# Philips HomeID Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/renaudallard/homeassistant_philips_homeid)
[![GitHub Release](https://img.shields.io/github/v/release/renaudallard/homeassistant_philips_homeid)](https://github.com/renaudallard/homeassistant_philips_homeid/releases)
[![License](https://img.shields.io/github/license/renaudallard/homeassistant_philips_homeid)](LICENSE)

Control your Philips domestic appliances locally through Home Assistant. No cloud dependency after initial setup.

---

## Supported Devices

| Category | Models | Notes |
|----------|--------|-------|
| **Air Purifiers** | AC0650, AC0651, AC series | Local network connectivity required |
| **Air Fryers** | HD9200, HD9255, HD9280, HD9285, HD9875, HD9876 | Single basket models |
| **Dual Basket Air Fryers** | HD9880 | Independent basket control |

> **Note:** Espresso machines (EP series) use cloud-based communication and are not supported.

---

## Features

| Feature | Description |
|---------|-------------|
| **Local Control** | Direct communication over your local network |
| **Auto Discovery** | Automatic device detection via Zeroconf/SSDP |
| **Smart Polling** | 60s when idle, 10s while cooking |
| **Extrapolated Timers** | Smooth countdown updates between polls |
| **Dynamic Entities** | Sensors created only when device reports data |

### Air Purifiers
- Fan speed and preset modes (auto, manual, sleep, turbo, allergen, bacteria, night)
- Air quality sensors: PM1, PM2.5, PM10, TVOC, gas, allergen index
- Environment sensors: humidity, temperature
- Filter status: pre-filter, HEPA, carbon, humidifier wick
- Controls: power, child lock

### Air Fryers
- Cooking status, temperature (target/current), time remaining
- Controls: start, pause, stop, temperature, cook time
- Sensors: drawer state, preheat status, shake/flip reminders, keep warm
- Dual basket models: independent left/right basket monitoring

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

1. Download the latest release from [GitHub Releases](https://github.com/renaudallard/homeassistant_philips_homeid/releases)
2. Extract and copy `custom_components/philips_homeid` to your Home Assistant's `custom_components` directory
3. Restart Home Assistant

---

## Configuration

### Adding a Device

1. Go to **Settings** > **Devices & Services** > **Add Integration**
2. Search for **Philips HomeID**
3. Enter the device's IP address
4. The integration will attempt to pair with the device
5. If pairing fails, enter credentials manually (see below)

### Auto-Discovered Devices

Devices discovered via Zeroconf or SSDP will appear automatically. If the device is already paired with the HomeID app, you'll need to enter credentials manually.

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
| Binary Sensor | Filter Replace Required | Filter needs replacement |
| Binary Sensor | Water Tank Empty | Water tank status |
| Switch | Child Lock | Child lock control |

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
| Binary Sensor | Drawer | Drawer open/closed |
| Binary Sensor | Shake / Flip Reminder | Food reminders |
| Binary Sensor | Preheat Active | Preheat cycle status |
| Button | Start / Pause / Stop | Cooking controls |
| Number | Set Temperature / Cook Time | Adjustable settings |

</details>

<details>
<summary><b>Dual Basket Air Fryer (Additional)</b></summary>

| Type | Entity | Description |
|------|--------|-------------|
| Sensor | Left/Right Basket Status | Per-basket cooking state |
| Sensor | Left/Right Basket Temperature | Per-basket target temp |
| Sensor | Left/Right Basket Time | Per-basket cook time |
| Binary Sensor | Left/Right Drawer | Per-basket drawer state |

</details>

> **Note:** Entities are automatically filtered by device type. Only relevant sensors appear for your specific model.

---

## Troubleshooting

### Device Not Found
- Ensure the device is on the same network as Home Assistant
- Verify the IP address is correct
- Check that the device is powered on and connected

### Pairing Fails
If the device is already paired with the Philips HomeID app, you need to extract credentials manually:

<details>
<summary><b>Obtaining Device Credentials</b></summary>

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
   - Navigate to: `homeid` > `network_node.db` > `network_node`
   - Find `client_id` and `client_secret` in the last columns
   - Enter these values during integration setup

</details>

---

## Technical Details

| Aspect | Details |
|--------|---------|
| Protocol | HTTPS with self-signed certificates |
| Authentication | PHILIPS-Condor challenge-response (SHA256) |
| Discovery | Zeroconf (`_philipscondor._tcp.local.`) / SSDP (`urn:philips-com:device:DiProduct:1`) |

---

## Disclaimer

This is an unofficial integration and is not affiliated with Philips or Versuni. Use at your own risk.

---

## License

BSD 2-Clause License - see [LICENSE](LICENSE) for details.

Copyright (c) 2025, Renaud Allard <renaud@allard.it>
