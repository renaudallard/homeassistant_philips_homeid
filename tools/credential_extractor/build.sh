#!/bin/bash
#
# Build the Philips HomeID credential extractor DEX file.
#
# Requirements:
#   - Java 17+ (javac)
#   - Android SDK build-tools (d8)
#
# The ANDROID_HOME environment variable should point to the Android SDK,
# or build-tools must be on the PATH.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Find d8
if command -v d8 >/dev/null 2>&1; then
    D8=d8
elif [ -n "$ANDROID_HOME" ]; then
    D8=$(find "$ANDROID_HOME/build-tools" -name d8 -type f 2>/dev/null | sort -V | tail -1)
elif [ -d "$HOME/Android/Sdk/build-tools" ]; then
    D8=$(find "$HOME/Android/Sdk/build-tools" -name d8 -type f 2>/dev/null | sort -V | tail -1)
fi

if [ -z "$D8" ] || [ ! -x "$D8" ]; then
    echo "Error: d8 not found"
    echo "Install Android SDK build-tools or set ANDROID_HOME"
    exit 1
fi

echo "Using d8: $D8"

# Compile Java source against Android stubs
echo "Compiling ExtractCreds.java..."
javac -source 11 -target 11 \
    -cp stubs \
    -d build \
    ExtractCreds.java

# Convert to DEX
echo "Converting to DEX..."
"$D8" \
    --min-api 26 \
    --output build \
    build/ExtractCreds.class

# Copy result
cp build/classes.dex extractor.dex

echo "Built: extractor.dex"
echo ""
echo "To use on a rooted device:"
echo "  adb push extractor.dex extract_creds.sh /data/local/tmp/"
echo "  adb shell su -c 'sh /data/local/tmp/extract_creds.sh'"
