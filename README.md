# Philips HomeID Integration for Home Assistant

This custom integration allows you to control Philips domestic appliances through Home Assistant using local network communication. Devices are controlled directly without cloud dependency after initial setup.

## Supported Devices

### Air Purifiers
- **AC0650** - Muji Air Purifier
- **AC0651** - Muji Plus Air Purifier
- Other AC series air purifiers with local network connectivity

### Air Fryers
- **HD9200** - Essential Airfryer
- **HD9255** - Essential Airfryer XL
- **HD9280** - Premium Airfryer XXL
- **HD9285** - Premium Airfryer XXL
- **HD9875** - Airfryer XXL Connected
- **HD9876** - Airfryer XXL Connected
- **HD9880** - Dual Basket Airfryer (supports both baskets)

Note: Espresso machines (EP series) use cloud-based communication and are not supported by this local-only integration.

## Features

- **Local Control** - Devices are controlled directly over your local network (no cloud required after setup)
- Automatic device discovery via Zeroconf/SSDP
- **Smart Polling** - Polls every 60 seconds when idle, increases to every 10 seconds while airfryer is cooking for responsive status updates
- **Air Purifiers**: Fan speed, preset modes, comprehensive air quality sensors (PM1, PM2.5, PM10, TVOC, gas), humidity, temperature, multiple filter status sensors, child lock, runtime tracking
- **Air Fryers**: Cooking status, target/current temperature, time control, start/pause/stop buttons, drawer sensor, preheat status, shake/flip reminders, keep warm
- **Dual Basket Air Fryers**: Independent control and monitoring of left and right baskets

## Installation

### HACS (Recommended)

1. Add this repository to HACS as a custom repository
2. Search for "Philips HomeID" and install
3. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/philips_homeid` folder to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

## Configuration

### Adding a Device

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "Philips HomeID"
3. Enter the device's IP address
4. The integration will attempt to pair with the device
5. If pairing fails (device already paired with the HomeID app), you can enter credentials manually (see "Obtaining Device Credentials Manually" below)

### Discovered Devices

Devices discovered automatically on your network via Zeroconf or SSDP will prompt for pairing. If pairing fails (device already paired with the HomeID app), you can enter credentials manually (see "Obtaining Device Credentials Manually" below).

## Entities Created

### Air Purifiers

| Entity Type | Entity Name | Description |
|------------|-------------|-------------|
| Fan | Air Purifier | Main control with speed and preset modes |
| Sensor | PM1.0 | Ultra-fine particulate matter (if supported) |
| Sensor | PM2.5 | Fine particulate matter reading |
| Sensor | PM10 | Coarse particulate matter (if supported) |
| Sensor | Air Quality Index | Indoor air quality index (IAQL) |
| Sensor | Total VOC | Total volatile organic compounds (if supported) |
| Sensor | Gas Level | Gas/formaldehyde level (if supported) |
| Sensor | Allergen Index | Allergen level indicator (if supported) |
| Sensor | Humidity | Relative humidity |
| Sensor | Temperature | Indoor temperature |
| Sensor | Pre-filter | Pre-filter remaining life |
| Sensor | HEPA Filter | HEPA filter remaining life |
| Sensor | Carbon Filter | Carbon filter remaining life |
| Sensor | Humidifier Wick | Humidifier wick remaining life (if supported) |
| Sensor | Water Level | Water tank level for humidifiers (if supported) |
| Sensor | Display Brightness | Display brightness level |
| Sensor | Total Runtime | Total device runtime in hours |
| Sensor | Mode | Current operating mode |
| Sensor | Fan Speed | Current fan speed setting |
| Sensor | Error Code | Device error status |
| Binary Sensor | Filter Replace Required | Filter needs replacement (if supported) |
| Binary Sensor | Water Tank Empty | Water tank is empty (if supported) |
| Switch | Power | Power on/off |
| Switch | Child Lock | Child lock on/off |

### Air Fryers

| Entity Type | Entity Name | Description |
|------------|-------------|-------------|
| Sensor | Cooking Status | Current cooking state |
| Sensor | Target Temperature | Set cooking temperature |
| Sensor | Current Temperature | Actual internal temperature (if supported) |
| Sensor | Total Cook Time | Total cooking duration |
| Sensor | Time Remaining | Remaining cook time |
| Sensor | Preset | Selected cooking preset |
| Sensor | Recipe | Current recipe name |
| Sensor | Error Code | Device error status |
| Sensor | Preheat Status | Preheat progress (if supported) |
| Sensor | Keep Warm | Keep warm setting (if supported) |
| Binary Sensor | Drawer | Drawer open/closed state |
| Binary Sensor | Shake Reminder | Shake food reminder active (if supported) |
| Binary Sensor | Flip Reminder | Flip food reminder active (if supported) |
| Binary Sensor | Preheat Active | Preheat cycle running (if supported) |
| Button | Start Cooking | Start the cooking cycle |
| Button | Pause | Pause cooking |
| Button | Stop | Stop cooking |
| Number | Set Temperature | Adjust cooking temperature |
| Number | Set Cook Time | Adjust cooking duration |

### Dual Basket Air Fryers (HD9880 and similar)

In addition to the standard airfryer entities, dual basket models include:

| Entity Type | Entity Name | Description |
|------------|-------------|-------------|
| Sensor | Left Basket Status | Left basket cooking state |
| Sensor | Left Basket Temperature | Left basket target temperature |
| Sensor | Left Basket Time | Left basket cook time |
| Sensor | Right Basket Status | Right basket cooking state |
| Sensor | Right Basket Temperature | Right basket target temperature |
| Sensor | Right Basket Time | Right basket cook time |
| Binary Sensor | Left Basket Drawer | Left drawer open/closed |
| Binary Sensor | Right Basket Drawer | Right drawer open/closed |

Note: Entities are automatically filtered based on your device type. Air purifier sensors will not appear for air fryers and vice versa. Dual basket sensors only appear for dual basket models (HD9880). Entities marked "(if supported)" will only appear if your specific device model provides that data.

### Preset Modes for Air Purifiers

- `auto` - Automatic mode based on air quality
- `manual` - Manual fan speed control
- `sleep` - Sleep mode (quiet operation)
- `turbo` - Maximum power
- `allergen` - Allergen removal mode
- `bacteria` - Bacteria/virus removal mode
- `night` - Night mode

## Troubleshooting

### Device Not Found

- Devices must be on the same network as Home Assistant
- Check that the device's IP address is correct
- Ensure the device is powered on and connected to your network

## Technical Details

### Local API

Devices are controlled via a local HTTPS API with challenge-response authentication:
- Protocol: HTTPS (self-signed certificates)
- Auth scheme: PHILIPS-Condor (challenge-response with SHA256)
- Credentials: Device-specific `client_id` and `client_secret` (base64-encoded)

### Device Discovery

- **Zeroconf**: `_philipscondor._tcp.local.`
- **SSDP**: `urn:philips-com:device:DiProduct:1`

### Obtaining Device Credentials Manually

If pairing fails (device already paired with the HomeID app), you can obtain the `client_id` and `client_secret` from the HomeID app's local database using an Android emulator:

1. **Install Android x86 in a VM** (e.g., VirtualBox or VMware)
   - Important: The Android VM and your Philips device must be on the same network and subnet

2. **Set up the Android environment**
   - Install the Philips HomeID app from the Play Store
   - Update Chrome via the Play Store (required for authentication)
   - Pair your device with the HomeID app

3. **Install SQLite Database Editor**
   - Download from Play Store: [SQLite Database Editor](https://play.google.com/store/apps/details?id=com.tomminosoftware.sqliteeditor)

4. **Grant root access**
   - Open SQLite Database Editor and allow root permissions when prompted
   - If root access fails, enable root in Android x86 Developer Settings first

5. **Extract the credentials**
   - In SQLite Database Editor, navigate to: `homeid` → `network_node.db` → `network_node`
   - The second-to-last and last columns contain `client_id` and `client_secret`
   - Use these values when manually entering credentials during integration setup

## Development Notes

This integration was created by reverse-engineering the Philips HomeID Android app. Key findings:

1. **Local Control**: Devices expose a local HTTPS API for direct control without cloud dependency
2. **Authentication**: Uses PHILIPS-Condor challenge-response scheme (SHA256 hash of challenge + client_id + client_secret)
3. **Credentials**: Stored in the HomeID app's SQLite database (`network_node.db`)
4. **Device Types**: Different device families share the same API structure

## Disclaimer

This is an unofficial integration and is not affiliated with Philips or Versuni. Use at your own risk.

## License

BSD 2-Clause License - see [LICENSE](LICENSE) for details.

Copyright (c) 2025, Renaud Allard <renaud@allard.it>
