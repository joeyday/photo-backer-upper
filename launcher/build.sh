#!/bin/sh
# Build and sign the launcher app into ~/Applications/Photo Backer Upper.app.
# Set SIGN_IDENTITY to a signing identity (see: security find-identity -v -p codesigning) so
# the macOS privacy grants survive rebuilds. Without it the app is signed ad hoc ("-"), and
# the signature changes with every build, so macOS may ask for the permissions again.
set -eu
identity="${SIGN_IDENTITY:--}"
here="$(cd "$(dirname "$0")" && pwd)"
app="${APP_PATH:-$HOME/Applications/Photo Backer Upper.app}"

rm -rf "$app"
mkdir -p "$app/Contents/MacOS"
cp "$here/Info.plist" "$app/Contents/Info.plist"
swiftc -O "$here/PhotoBackerUpper.swift" -o "$app/Contents/MacOS/PhotoBackerUpper"
# Hardened runtime + entitlements on every build, so dev builds behave like the notarized
# release. A secure timestamp (needed for notarization) only works with a real identity.
timestamp="--timestamp=none"
[ "$identity" != "-" ] && timestamp="--timestamp"
codesign --force --sign "$identity" --identifier local.photo-backer-upper.launcher \
    --options runtime $timestamp --entitlements "$here/entitlements.plist" "$app"
codesign --verify --verbose "$app"
echo "Built: $app"
