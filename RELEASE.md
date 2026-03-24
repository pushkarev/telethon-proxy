# Release Guide

This repo builds a macOS Electron desktop app named `Aardvark`.

The release flow below produces:

- a signed app bundle
- a notarized and stapled app bundle
- a packaged `.dmg` and `.zip`

## Prerequisites

- macOS with Xcode command line tools installed
- Node dependencies installed with `npm install`
- a valid `Developer ID Application` certificate in Keychain
- a working Apple notarization credential profile in Keychain

Current working notarization profile on this machine:

```bash
telethon-proxy-notary
```

You can verify that profile with:

```bash
xcrun notarytool history --keychain-profile telethon-proxy-notary
```

## Build A Signed And Notarized Release

From the repo root:

```bash
APPLE_KEYCHAIN_PROFILE=telethon-proxy-notary npm run app:dist
```

This uses:

- `electron-builder` from `package.json`
- `electron/notarize.mjs` as the `afterSign` hook
- `Developer ID Application` signing from `package.json`

Artifacts are written to:

```text
dist/electron/
```

The main release files are:

- `dist/electron/Aardvark-<version>-arm64.dmg`
- `dist/electron/Aardvark-<version>-arm64-mac.zip`
- `dist/electron/mac-arm64/Aardvark.app`

## Verify The App Bundle

Check the signature:

```bash
codesign -dv --verbose=4 dist/electron/mac-arm64/Aardvark.app
```

Expected signals:

- `Authority=Developer ID Application: PushPlayLabs Inc (N975558CUS)`
- `Runtime Version=...`
- `Notarization Ticket=stapled`

Validate the stapled ticket:

```bash
xcrun stapler validate dist/electron/mac-arm64/Aardvark.app
```

Gatekeeper verification:

```bash
spctl -a -vvv dist/electron/mac-arm64/Aardvark.app
```

Expected result:

```text
accepted
source=Notarized Developer ID
```

## Optional: Notarize The DMG Itself

`electron-builder` notarizes the `.app` before packaging the final `.dmg`.
That means the app inside the disk image is notarized, but the outer `.dmg`
may not yet have its own stapled ticket.

If you want the DMG container itself notarized and stapled too:

1. Submit the DMG:

```bash
VERSION="$(node -p \"require('./package.json').version\")"
xcrun notarytool submit "dist/electron/Aardvark-${VERSION}-arm64.dmg" \
  --keychain-profile telethon-proxy-notary \
  --wait
```

2. Staple the DMG:

```bash
xcrun stapler staple "dist/electron/Aardvark-${VERSION}-arm64.dmg"
```

3. Validate the stapled DMG:

```bash
xcrun stapler validate "dist/electron/Aardvark-${VERSION}-arm64.dmg"
```

## Checksums

Generate checksums before publishing:

```bash
shasum -a 256 "dist/electron/Aardvark-${VERSION}-arm64.dmg"
shasum -a 256 "dist/electron/Aardvark-${VERSION}-arm64-mac.zip"
```

## Troubleshooting

If notarization is skipped, check that one of these auth paths is configured:

- `APPLE_KEYCHAIN_PROFILE`
- `APPLE_API_KEY`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER`
- `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`

If signing fails with a timestamp error:

- retry the build later
- verify Apple signing services are reachable
- confirm the `Developer ID Application` certificate is still valid

If `spctl` says `Unnotarized Developer ID`:

- the app is signed but not notarized
- rerun the build with `APPLE_KEYCHAIN_PROFILE=telethon-proxy-notary`

## Local Release Notes

The current packaging config lives in:

- `package.json`
- `electron/notarize.mjs`
- `electron/entitlements.mac.plist`
- `electron/entitlements.mac.inherit.plist`
