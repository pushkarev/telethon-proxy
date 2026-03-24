import path from "node:path";

import { notarize } from "@electron/notarize";


function notarizeOptions(appPath) {
  const keychainProfile = String(process.env.APPLE_KEYCHAIN_PROFILE || "").trim();
  if (keychainProfile) {
    const keychain = String(process.env.APPLE_KEYCHAIN || "").trim();
    return {
      appPath,
      keychainProfile,
      ...(keychain ? { keychain } : {}),
    };
  }

  const appleApiKey = String(process.env.APPLE_API_KEY || "").trim();
  const appleApiKeyId = String(process.env.APPLE_API_KEY_ID || "").trim();
  const appleApiIssuer = String(process.env.APPLE_API_ISSUER || "").trim();
  if (appleApiKey && appleApiKeyId && appleApiIssuer) {
    return {
      appPath,
      appleApiKey,
      appleApiKeyId,
      appleApiIssuer,
    };
  }

  const appleId = String(process.env.APPLE_ID || "").trim();
  const appleIdPassword = String(process.env.APPLE_APP_SPECIFIC_PASSWORD || "").trim();
  const teamId = String(process.env.APPLE_TEAM_ID || "").trim();
  if (appleId && appleIdPassword && teamId) {
    return {
      appPath,
      appleId,
      appleIdPassword,
      teamId,
    };
  }

  return null;
}


export default async function notarizeMac(context) {
  const { electronPlatformName, appOutDir, packager } = context;
  if (electronPlatformName !== "darwin") {
    return;
  }
  if (process.env.SKIP_NOTARIZE === "1") {
    console.log("[notarize] Skipping because SKIP_NOTARIZE=1.");
    return;
  }

  const appPath = path.join(appOutDir, `${packager.appInfo.productFilename}.app`);
  const options = notarizeOptions(appPath);
  if (!options) {
    console.warn(
      "[notarize] No Apple notarization credentials were configured. " +
        "Set APPLE_KEYCHAIN_PROFILE, or APPLE_API_KEY/APPLE_API_KEY_ID/APPLE_API_ISSUER, " +
        "or APPLE_ID/APPLE_APP_SPECIFIC_PASSWORD/APPLE_TEAM_ID.",
    );
    return;
  }

  console.log(`[notarize] Submitting ${appPath} for notarization...`);
  await notarize(options);
  console.log("[notarize] Notarization complete.");
}
