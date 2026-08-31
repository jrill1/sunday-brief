#!/usr/bin/env bash
# Builds and (re)installs the Sunday Brief Opener app on a connected,
# USB-debugging-enabled Android phone. See README.md for one-time setup.
set -euo pipefail
cd "$(dirname "$0")"

SDK="${ANDROID_SDK_ROOT:-/opt/homebrew/share/android-commandlinetools}"
BT="$SDK/build-tools/34.0.0"
PLATFORM_JAR="$SDK/platforms/android-34/android.jar"

if [ -z "${JAVA_HOME:-}" ]; then
    JAVA_HOME="$(/usr/libexec/java_home -v 21 2>/dev/null || true)"
fi
if [ -z "$JAVA_HOME" ]; then
    echo "error: no JDK found. Run: brew install --cask temurin" >&2
    exit 1
fi
export PATH="$JAVA_HOME/bin:$PATH"

rm -rf obj dist
mkdir -p obj dist

echo "== aapt2 link =="
"$BT/aapt2" link -o dist/base-unsigned.apk -I "$PLATFORM_JAR" \
    --manifest AndroidManifest.xml --min-sdk-version 24 --target-sdk-version 34

echo "== javac =="
javac --release 11 -cp "$PLATFORM_JAR" -d obj src/com/sundaybrief/opener/OpenActivity.java

echo "== d8 =="
"$BT/d8" --lib "$PLATFORM_JAR" --output obj obj/com/sundaybrief/opener/OpenActivity.class

cp obj/classes.dex dist/
(cd dist && zip -q base-unsigned.apk classes.dex)

echo "== zipalign =="
"$BT/zipalign" -f -p 4 dist/base-unsigned.apk dist/aligned.apk

if [ ! -f dist/debug.keystore ]; then
    echo "== generating debug keystore (one-time) =="
    keytool -genkeypair -v -keystore dist/debug.keystore -storepass android \
        -alias androiddebugkey -keypass android -keyalg RSA -keysize 2048 \
        -validity 10000 -dname "CN=Sunday Brief, OU=Dev, O=Home, L=City, S=NJ, C=US"
fi

echo "== apksigner =="
"$BT/apksigner" sign --ks dist/debug.keystore --ks-pass pass:android \
    --key-pass pass:android --out dist/signed.apk dist/aligned.apk

echo "== installing to connected device =="
adb install -r dist/signed.apk

echo "done: dist/signed.apk"
