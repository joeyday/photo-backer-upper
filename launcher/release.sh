#!/bin/sh
# Build, sign with a Developer ID certificate, notarize and staple the launcher app, and
# produce build/release/PhotoBackerUpper.zip ready to attach to a GitHub release.
#
# One-time setup (see the README's "Releasing the launcher" section):
#   1. A "Developer ID Application" certificate in your keychain.
#   2. Notarization credentials stored under a keychain profile:
#        xcrun notarytool store-credentials pback-notary
#
# Usage:  SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" sh launcher/release.sh
set -eu
: "${SIGN_IDENTITY:?set SIGN_IDENTITY to your Developer ID Application identity}"
case "$SIGN_IDENTITY" in
  "Developer ID Application"*|[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]*) ;;
  *) echo "SIGN_IDENTITY should be a Developer ID Application identity (or its hash)." >&2; exit 2 ;;
esac
profile="${NOTARY_PROFILE:-pback-notary}"
here="$(cd "$(dirname "$0")" && pwd)"
out="$here/../build/release"
app="$out/Photo Backer Upper.app"
zip="$out/PhotoBackerUpper.zip"

rm -rf "$out"
mkdir -p "$out"
APP_PATH="$app" SIGN_IDENTITY="$SIGN_IDENTITY" sh "$here/build.sh"

# Notarization takes a zip; ditto keeps the app bundle intact.
ditto -c -k --keepParent "$app" "$out/submit.zip"
xcrun notarytool submit "$out/submit.zip" --keychain-profile "$profile" --wait
xcrun stapler staple "$app"
rm "$out/submit.zip"

# Final zip contains the stapled app, so it opens on a Mac that's offline.
ditto -c -k --keepParent "$app" "$zip"
spctl --assess --type execute --verbose "$app"
echo "Release build: $zip"
