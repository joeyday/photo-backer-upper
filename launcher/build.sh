#!/bin/sh
# Build and sign the launcher app into ~/Applications/Photo Backer Upper.app.
# Set SIGN_IDENTITY to a signing identity (see: security find-identity -v -p codesigning) so
# the macOS privacy grants survive rebuilds. Without it the app is signed ad hoc ("-"), and
# the signature changes with every build, so macOS may ask for the permissions again.
set -eu
identity="${SIGN_IDENTITY:--}"
here="$(cd "$(dirname "$0")" && pwd)"
app="$HOME/Applications/Photo Backer Upper.app"

rm -rf "$app"
mkdir -p "$app/Contents/MacOS"
cp "$here/Info.plist" "$app/Contents/Info.plist"
swiftc -O "$here/PhotoBackerUpper.swift" -o "$app/Contents/MacOS/PhotoBackerUpper"
codesign --force --sign "$identity" --identifier local.photo-backer-upper.launcher "$app"
codesign --verify --verbose "$app"
echo "Built: $app"
