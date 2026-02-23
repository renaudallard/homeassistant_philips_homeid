#!/system/bin/sh
#
# Philips HomeID Credential Extractor
#
# Extracts client_id, client_secret, and other credentials from the
# Philips HomeID app's EncryptedSharedPreferences on a rooted Android device.
#
# Usage:
#   adb push extractor.dex extract_creds.sh /data/local/tmp/
#   adb shell
#   su
#   sh /data/local/tmp/extract_creds.sh [--dump-all] [MAC_ADDRESS]
#
# The MAC address is optional. If the SQLite database is empty (newer
# firmwares), pass the device MAC so the tool can look up credentials
# in the encrypted preferences. Example:
#   sh /data/local/tmp/extract_creds.sh e4:bc:96:00:00:00
#
# Use --dump-all to dump all entries from encrypted stores (not just
# known credential keys). Useful for diagnostics when credentials are
# stored under unexpected key names:
#   sh /data/local/tmp/extract_creds.sh --dump-all e4:bc:96:00:00:00
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

# Try to find the app's SELinux context for runcon.
# Running under the app's context instead of u:r:magisk:s0 avoids
# SELinux denials when accessing AndroidKeyStore crypto operations.
APP_SECONTEXT=""
if [ "$(getenforce 2>/dev/null)" = "Enforcing" ]; then
    # Grab context from the running app process.
    # pidof may return multiple space-separated PIDs — take the first.
    APP_PID=$(pidof "$PKG" 2>/dev/null)
    APP_PID=${APP_PID%% *}
    if [ -n "$APP_PID" ]; then
        APP_SECONTEXT=$(cat "/proc/$APP_PID/attr/current" 2>/dev/null)
    fi
fi

echo "Package:  $PKG"
echo "UID:      $APP_UID"
echo "APK:      $APK_PATH"
if [ -n "$APP_SECONTEXT" ]; then
    echo "SELinux:  $APP_SECONTEXT (from app)"
else
    echo "SELinux:  $(cat /proc/self/attr/current 2>/dev/null || echo 'unknown')"
fi
echo ""

# Run the extractor as the app's UID
# Only our small DEX is on the CLASSPATH — the Philips APK is NOT included
# to avoid app_process crashing during DEX optimization of the huge APK.
# The Philips APK's classes are loaded at runtime via createPackageContext().
# Running as the app's UID gives access to its Android Keystore keys.
#
# Write a runner script since su syntax varies across root managers:
# - Magisk: su <uid> -c 'cmd' works
# - SuperSU/others: su <uid> -c may fail with "Cannot execute -c"
# Using a script file is compatible with all implementations.
#
# If we found the app's SELinux context, try running with runcon first.
# The Java extractor always exits 0 (catches all exceptions), so any
# non-zero exit means the process didn't start (e.g. runcon denied the
# exec on app_process) — safe to retry without runcon.
RUNNER="/data/local/tmp/_extract_run.sh"
EXTRA_ARGS="$*"
SELINUX_WAS_ENFORCING=""

if [ -n "$APP_SECONTEXT" ]; then
    cat > "$RUNNER" << SCRIPT
#!/system/bin/sh
export CLASSPATH=$DEX
exec runcon $APP_SECONTEXT app_process / ExtractCreds $EXTRA_ARGS
SCRIPT
    chmod 755 "$RUNNER"
    su "$APP_UID" "$RUNNER" 2>&1
    EXIT_CODE=$?

    if [ "$EXIT_CODE" != "0" ]; then
        echo ""
        echo "[WARN] runcon failed (exit $EXIT_CODE), retrying without SELinux context switch..."
        echo ""
        APP_SECONTEXT=""
    fi
fi

if [ -z "$APP_SECONTEXT" ]; then
    # Temporarily set SELinux to Permissive so app_process can access
    # AndroidKeyStore without the app's SELinux context.
    if [ "$(getenforce 2>/dev/null)" = "Enforcing" ]; then
        setenforce 0
        SELINUX_WAS_ENFORCING="1"
        echo "[INFO] Temporarily set SELinux to Permissive for Keystore access"
    fi

    cat > "$RUNNER" << SCRIPT
#!/system/bin/sh
export CLASSPATH=$DEX
exec app_process / ExtractCreds $EXTRA_ARGS
SCRIPT
    chmod 755 "$RUNNER"
    su "$APP_UID" "$RUNNER" 2>&1
    EXIT_CODE=$?
fi

# Restore SELinux if we changed it
if [ -n "$SELINUX_WAS_ENFORCING" ]; then
    setenforce 1
    echo "[INFO] SELinux restored to Enforcing"
fi

rm -f "$RUNNER"

if [ "$EXIT_CODE" != "0" ]; then
    echo ""
    echo "Error: extractor exited with code $EXIT_CODE"
    echo "Debug info:"
    echo "  DEX file: $(ls -la $DEX 2>&1)"
    echo "  APK file: $(ls -la $APK_PATH 2>&1)"
    echo "  Running as UID: $APP_UID"
    echo "  SELinux: $(getenforce 2>/dev/null || echo 'unknown')"
fi
