#!/system/bin/sh
#
# Philips HomeID Credential Extractor
#
# Extracts client_id, client_secret, and other credentials from the
# Philips HomeID app's EncryptedSharedPreferences on a rooted Android device.
#
# Usage:
#   adb push extractor.dex extract_creds.sh /data/local/tmp/
#   adb shell su -c "sh /data/local/tmp/extract_creds.sh"
#

PKG="com.philips.ka.oneka.app"
DEX="/data/local/tmp/extractor.dex"

# Check we're running as root
if [ "$(id -u)" != "0" ]; then
    echo "Error: must run as root (use 'su -c')"
    exit 1
fi

# Check DEX file exists
if [ ! -f "$DEX" ]; then
    echo "Error: $DEX not found"
    echo "Push it with: adb push extractor.dex /data/local/tmp/"
    exit 1
fi

# Check Philips app is installed
if [ ! -d "/data/data/$PKG" ]; then
    echo "Error: Philips HomeID app ($PKG) not installed"
    exit 1
fi

# Get the app's UID
APP_UID=$(stat -c %u "/data/data/$PKG" 2>/dev/null)
if [ -z "$APP_UID" ]; then
    echo "Error: cannot determine app UID"
    exit 1
fi

# Get the APK path
APK_PATH=$(pm path "$PKG" 2>/dev/null | head -1 | sed 's/^package://')
if [ -z "$APK_PATH" ]; then
    echo "Error: cannot find APK path for $PKG"
    exit 1
fi

echo "Package:  $PKG"
echo "UID:      $APP_UID"
echo "APK:      $APK_PATH"
echo ""

# Run the extractor as the app's UID
# Only our small DEX is on the CLASSPATH — the 120MB Philips APK is NOT included
# to avoid app_process crashing during DEX optimization of the huge APK.
# The Philips APK's classes are loaded at runtime via createPackageContext().
# Running as the app's UID gives access to its Android Keystore keys.
su "$APP_UID" -c "CLASSPATH=$DEX app_process / ExtractCreds" 2>&1
EXIT_CODE=$?
if [ "$EXIT_CODE" != "0" ]; then
    echo ""
    echo "Error: extractor exited with code $EXIT_CODE"
    echo "Debug info:"
    echo "  DEX file: $(ls -la $DEX 2>&1)"
    echo "  APK file: $(ls -la $APK_PATH 2>&1)"
    echo "  Running as: $(su $APP_UID -c 'id' 2>&1)"
    echo "  SELinux: $(getenforce 2>/dev/null || echo 'unknown')"
fi
