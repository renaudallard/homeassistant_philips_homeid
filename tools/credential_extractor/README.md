# Philips HomeID Credential Extractor

Extracts `client_id`, `client_secret`, and other credentials from the Philips HomeID Android app on a rooted device.

## Background

Some Philips devices (e.g., HD9285 with firmware 0.5.6/1.1.8) store credentials exclusively in `EncryptedSharedPreferences`, which are encrypted with Android Keystore keys tied to the app's UID. These credentials cannot be read by simply opening the XML files — they must be decrypted using the Android Keystore, which is only accessible when running as the same UID as the Philips app.

This tool runs a small DEX program via `app_process` as the Philips app's UID, which gives it access to the Keystore and the app's encrypted preferences.

## Requirements

- **Rooted Android device** (or Android x86 VM with root) with the Philips HomeID app installed and paired with your device
- **adb** on your computer
- For building from source: Java 17+ and Android SDK build-tools

## Usage

### 1. Download the files

The pre-built `extractor.dex` and `extract_creds.sh` are in the [`tools/credential_extractor/`](https://github.com/renaudallard/homeassistant_philips_homeid/tree/main/tools/credential_extractor) directory of the repository. Download both files, then push them to the device:

```sh
adb push extractor.dex /data/local/tmp/
adb push extract_creds.sh /data/local/tmp/
```

### 2. Run the extractor

```sh
adb shell
su
sh /data/local/tmp/extract_creds.sh
```

### 3. Read the output

The tool tries multiple extraction methods. Look for these in the output:

From the **SQLite database** (older firmwares):
- `client_id` — the `client_id` for the integration
- `client_secret` — the `client_secret` for the integration
- `encryption_key` — needed for HTTP devices (e.g., HD9285)

From **EncryptedSharedPreferences** (newer firmwares):
- `DEVICE_CLIENT_ID` — the `client_id` for the integration
- `DEVICE_CLIENT_SECRET` — the `client_secret` for the integration
- `DEVICE_CPP_ID` / `DEVICE_HSDP_ID` — device identifiers (not needed for setup)
- Keys are prefixed with the device's MAC address (e.g., `e4:bc:96:1e:7f:b1DEVICE_CLIENT_ID`)

## Building from Source

If you want to rebuild the DEX file:

```sh
./build.sh
```

This requires:
- Java 17+ (`javac`)
- Android SDK build-tools (`d8`) — set `ANDROID_HOME` or have `d8` on your PATH

## How It Works

1. The shell script finds the Philips app's UID and APK path on the device
2. It runs `app_process` as the app's UID with both our DEX and the Philips APK on the classpath
3. The Java extractor tries multiple methods in order:
   - **SQLite database** (`network_node.db`): Queries all rows from the `network_node` table. This is where older firmwares store credentials in plain text.
   - **StoragePreferences** (`COMMUNICATION_LIB_PREFERENCES`): Uses the app's own class to open Tink EncryptedSharedPreferences. Since we're running as the same UID, the Android Keystore transparently provides the decryption keys.
   - **Core SecurePreferences** (`ONE_KA_ENCRYPTED_PREFERENCES`): Opens the app preferences which use an additional XOR encryption layer on top of Tink.
   - **Plain SharedPreferences**: Fallback that reads unencrypted preference files.

### Encryption layers

The Philips app uses two layers of encryption for credential storage:

1. **EncryptedSharedPreferences** (Android Jetpack Security / Google Tink):
   - Master key: `_androidx_security_master_key_` in Android Keystore (AES-256-GCM)
   - Key encryption: AES-256-SIV (deterministic)
   - Value encryption: AES-256-GCM

2. **XOR layer** (app-specific, in `ONE_KA_ENCRYPTED_PREFERENCES` only):
   - Keys split into 4 SHA-1 hashed parts (`SHA1("key_0")` through `SHA1("key_3")`)
   - Values XOR'd with package name (`com.philips.ka.oneka.app`), hex-encoded, then split into 4 chunks

The WiFi credentials in `COMMUNICATION_LIB_PREFERENCES` only use layer 1, so they're directly readable after Tink decryption.

## Troubleshooting

### "StoragePreferences class not found"
The Philips APK is not on the classpath. Make sure the app is installed and the shell script can find it via `pm path`.

### "cannot determine app UID"
The Philips app's data directory doesn't exist. Make sure the app is installed.

### KeyStore or decryption errors
The Android Keystore keys might be inaccessible. This can happen if:
- The app was recently reinstalled (keys regenerated)
- You're running on a different Android user profile
- SELinux is blocking access — try `setenforce 0` temporarily

### Empty output
The app might not have stored any credentials yet. Make sure you've paired a device with the Philips HomeID app first.
