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

On newer firmwares where the SQLite database is empty, the tool cannot automatically discover the device MAC address. In that case, pass it as an argument (you can find it in the Philips HomeID app or on the device label):

```sh
sh /data/local/tmp/extract_creds.sh e4:bc:96:00:00:00
```

### 3. Read the output

The tool tries four extraction methods. Example output:

```
Philips HomeID Credential Extractor
====================================

--- Method 1: SQLite Database ---
  Row 1:
    cppid = e4:bc:96:00:00:00
    model_id = HD9280
    client_id = abc123...
    client_secret = xyz789...

--- Method 2: Encrypted Preferences ---
  e4:bc:96:00:00:00DEVICE_CLIENT_ID = abc123...
  e4:bc:96:00:00:00DEVICE_CLIENT_SECRET = xyz789...
  e4:bc:96:00:00:00DEVICE_CPP_ID = e4:bc:96:00:00:00
  e4:bc:96:00:00:00DEVICE_HSDP_ID = xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  Found 4 entries

--- Method 3: Secure Preferences ---
  DEVICE_CLIENT_ID = abc123...
  DEVICE_CLIENT_SECRET = xyz789...
  (42 total entries in encrypted store)

--- Method 4: AES-CBC Preferences ---
  DEVICE_CLIENT_ID = abc123...
  DEVICE_CLIENT_SECRET = xyz789...
```

**Method 1 (SQLite Database)** — works on older firmwares:
- `client_id` — the `client_id` for the integration
- `client_secret` — the `client_secret` for the integration
- `encryption_key` — needed for HTTP devices (e.g., HD9285)

**Method 2 (Encrypted Preferences)** — works on newer firmwares:
- `DEVICE_CLIENT_ID` — the `client_id` for the integration
- `DEVICE_CLIENT_SECRET` — the `client_secret` for the integration
- `DEVICE_CPP_ID` / `DEVICE_HSDP_ID` — device identifiers (not needed for setup)
- Keys are prefixed with the device's MAC address

**Method 3 (Secure Preferences)** — additional encrypted store:
- Same credential keys as Method 2, with an extra XOR encryption layer
- MAC addresses discovered from Methods 1 and 2 are used automatically
- Also reports the total number of entries in the encrypted store for diagnostics

**Method 4 (AES-CBC Preferences)** — fallback encryption path:
- Used when the app falls back from Tink to AES-CBC encryption
- Does not require Android Keystore — uses password-based key derivation
- Reads from `COMMUNICATION_LIB_PREFERENCES` with the app's library password

Not all methods will return results on every device — you only need credentials from one method.

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
2. It runs `app_process` as the app's UID with our DEX on the classpath
3. The Java extractor bootstraps an Android runtime environment:
   - Creates an `ActivityThread` and `Application` via reflection
   - Bypasses hidden API restrictions for framework access
   - Registers the `AndroidKeyStore` security provider
   - Loads the Philips APK's classes via `createPackageContext`
4. It then tries four extraction methods:
   - **SQLite database** (`network_node.db`): Queries the `network_node` table where older firmwares store credentials in plain text.
   - **StoragePreferences** (`COMMUNICATION_LIB_PREFERENCES`): Uses the app's own class to open Tink EncryptedSharedPreferences. Since we're running as the same UID, the Android Keystore provides the decryption keys.
   - **SecurePreferences** (`ONE_KA_ENCRYPTED_PREFERENCES`): Uses the app's class which adds an XOR encryption layer on top of Tink. Includes a safety check — if the Android Keystore master key is missing, it skips this method to avoid data corruption.
   - **AES-CBC SecurePreferences** (`COMMUNICATION_LIB_PREFERENCES`): Uses the app's fallback encryption class which derives keys from a password via PBKDF2. Does not require Android Keystore access.

### Encryption layers

The Philips app uses multiple encryption approaches for credential storage:

1. **EncryptedSharedPreferences** (Android Jetpack Security / Google Tink):
   - Master key: `_androidx_security_master_key_` in Android Keystore (AES-256-GCM)
   - Key encryption: AES-256-SIV (deterministic)
   - Value encryption: AES-256-GCM

2. **XOR layer** (app-specific, in `ONE_KA_ENCRYPTED_PREFERENCES` only):
   - Keys split into 4 SHA-1 hashed parts (`SHA1("key_0")` through `SHA1("key_3")`)
   - Values XOR'd with package name (`com.philips.ka.oneka.app`), hex-encoded, then split into 4 chunks

3. **AES-CBC fallback** (used when Tink fails):
   - Password: `com.philips.ka.oneka.communication.library` (library package name)
   - Salt: app package name
   - Keys hashed with SHA-256 and Base64-encoded before storage
   - Values encrypted with AES-CBC with HMAC integrity check

The WiFi credentials in `COMMUNICATION_LIB_PREFERENCES` use either layer 1 (Tink) or layer 3 (AES-CBC fallback), depending on what the app chose at initialization.

## Troubleshooting

### "StoragePreferences class not found"
The Philips APK is not on the classpath. Make sure the app is installed and the shell script can find it via `pm path`.

### "cannot determine app UID"
The Philips app's data directory doesn't exist. Make sure the app is installed.

### "Application setup failed" or KeyStore errors
The Android Keystore keys might be inaccessible. This can happen if:
- The app was recently reinstalled (keys regenerated)
- You're running on a different Android user profile
- SELinux is blocking access — try `setenforce 0` temporarily

### Method 3: "[SKIP] Master key not in AndroidKeyStore"
The Android Keystore does not contain the Tink master key. This can happen if:
- You're running on a different device than where the app was paired
- The app was reinstalled or data was cleared
- SELinux is blocking Keystore access — try `setenforce 0` temporarily
The tool skips Method 3 in this case to prevent data corruption. Method 4 (AES-CBC) may still work.

### Method 3: "[WARN]" messages for credential keys
Tink decryption is failing for individual keys. This is usually caused by SELinux context issues — the `app_process` runs under `u:r:magisk:s0` instead of the app's normal context. Try `setenforce 0` temporarily.

### No credentials found in any method
The app might not have stored any credentials yet. Make sure you've:
- Paired a device with the Philips HomeID app **on the same device** where you're running the extractor
- Actually completed the pairing process (not just added the device via cloud)
The diagnostic entry counts (e.g., "42 total entries in encrypted store") help identify whether the encrypted stores contain data at all.
