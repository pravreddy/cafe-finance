#!/usr/bin/env bash
# One-shot setup for the Cafe Finance Android app (same recipe as guhya-pos-mobile-app).
#   ./setup.sh  -> install deps, generate android/, install our Kotlin, patch the manifest, build a debug APK
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; cd "$ROOT"
PKG_DIR="android/app/src/main/java/in/co/guhya/cafefinance"
MANIFEST="android/app/src/main/AndroidManifest.xml"
say() { printf "\n\033[1;32m==> %s\033[0m\n" "$1"; }
warn() { printf "\033[1;33m!! %s\033[0m\n" "$1"; }
command -v node >/dev/null || { echo "Node.js not found. brew install node"; exit 1; }

say "Installing Capacitor"; npm install
if [ ! -d android ]; then say "Generating the Android project"; npx cap add android; else say "android/ exists - syncing"; npx cap sync android; fi

say "Adding Kotlin support to the Gradle build"
python3 - <<'PY'
import pathlib, re
KOTLIN = "1.9.24"
p = pathlib.Path("android/build.gradle"); x = p.read_text()
if "kotlin-gradle-plugin" not in x:
    x = re.sub(r"(dependencies\s*\{)", r"\1\n        classpath 'org.jetbrains.kotlin:kotlin-gradle-plugin:" + KOTLIN + r"'", x, count=1); p.write_text(x)
p = pathlib.Path("android/app/build.gradle"); x = p.read_text(); ch = False
if "kotlin-android" not in x:
    x = re.sub(r"(apply plugin: 'com\.android\.application')", r"\1\napply plugin: 'kotlin-android'", x, count=1); ch = True
if "jvmTarget" not in x:
    m = re.search(r"VERSION_(\d+)", x); jvm = m.group(1) if m else "17"
    x = re.sub(r"(compileOptions\s*\{[^}]*\})", r"\1\n    kotlinOptions {\n        jvmTarget = '" + jvm + r"'\n    }", x, count=1, flags=re.S); ch = True
if "kotlin-stdlib" not in x:
    x = re.sub(r"(dependencies\s*\{)", r"\1\n    implementation 'org.jetbrains.kotlin:kotlin-stdlib:" + KOTLIN + r"'", x, count=1); ch = True
if ch: p.write_text(x)
print("   -> kotlin OK")
PY

say "Installing ShareIn (Kotlin)"
mkdir -p "$PKG_DIR"; cp native/MainActivity.kt native/ShareInPlugin.kt "$PKG_DIR/"; rm -f "$PKG_DIR/MainActivity.java"

say "Patching AndroidManifest.xml (share-sheet intent filters)"
python3 - "$MANIFEST" <<'PY'
import sys, re, pathlib
p = pathlib.Path(sys.argv[1]); x = p.read_text()
if 'android.permission.INTERNET' not in x:
    x = x.replace('</manifest>', '    <uses-permission android:name="android.permission.INTERNET" />\n</manifest>')
FILTERS = '''
            <!-- Cafe Finance in the share sheet: images and PDFs, one or many -->
            <intent-filter>
                <action android:name="android.intent.action.SEND" />
                <category android:name="android.intent.category.DEFAULT" />
                <data android:mimeType="image/*" />
                <data android:mimeType="application/pdf" />
            </intent-filter>
            <intent-filter>
                <action android:name="android.intent.action.SEND_MULTIPLE" />
                <category android:name="android.intent.category.DEFAULT" />
                <data android:mimeType="image/*" />
                <data android:mimeType="application/pdf" />
            </intent-filter>
'''
if 'android.intent.action.SEND' not in x:
    # put our filters inside the MainActivity <activity ...> element, after its launcher filter
    x = re.sub(r'(<activity[^>]*MainActivity[^>]*>.*?</intent-filter>)', r'\1' + FILTERS, x, count=1, flags=re.S)
# singleTask so a second share goes to onNewIntent instead of a new activity
x = re.sub(r'(<activity[^>]*MainActivity)(?![^>]*launchMode)', r'\1\n            android:launchMode="singleTask"', x, count=1)
p.write_text(x); print("   -> intent filters + singleTask OK")
PY

ICON_SRC="assets/icon.png"
if [ -f "$ICON_SRC" ]; then
  say "Generating the app icon"; TMP="$(mktemp -d)"
  sips -Z 800 "$ICON_SRC" --out "$TMP/mark.png" >/dev/null 2>&1
  sips "$TMP/mark.png" --padToHeightWidth 1024 1024 --padColor FFFFFF --out "$TMP/base.png" >/dev/null 2>&1
  for pair in "mdpi 48" "hdpi 72" "xhdpi 96" "xxhdpi 144" "xxxhdpi 192"; do set -- $pair
    DIR="android/app/src/main/res/mipmap-$1"; mkdir -p "$DIR"
    sips -z "$2" "$2" "$TMP/base.png" --out "$DIR/ic_launcher.png" >/dev/null 2>&1; cp "$DIR/ic_launcher.png" "$DIR/ic_launcher_round.png"
    FG=$(( $2 * 108 / 48 )); sips -z "$FG" "$FG" "$TMP/base.png" --out "$DIR/ic_launcher_foreground.png" >/dev/null 2>&1
  done; rm -rf "$TMP" android/app/src/main/res/mipmap-anydpi-v26
else
  warn "No assets/icon.png — default icon. Copy ../server/ledger/static/ledger/icon-512.png to assets/icon.png"
fi

say "Building a debug APK"
cd android && ./gradlew assembleDebug
unzip -l app/build/outputs/apk/debug/app-debug.apk | grep -q MainActivity || { warn "MainActivity not compiled — Kotlin plugin missing?"; exit 1; }
APK="$ROOT/android/app/build/outputs/apk/debug/app-debug.apk"
say "Done"; echo "APK: $APK"; echo "Install:  adb install -r \"$APK\"   (or send the APK on WhatsApp and open it on the phone)"
