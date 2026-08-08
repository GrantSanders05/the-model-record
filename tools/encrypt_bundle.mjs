/**
 * encrypt_bundle.mjs — encrypt the research bundle so it can be hosted in public.
 *
 * WHY THIS EXISTS
 * The research app needs to be reachable from a phone, for free, and the film
 * grades inside it must stay private. Those three requirements conflict on every
 * free host:
 *
 *   GitHub Pages   free, but a free account can only publish Pages from a PUBLIC
 *                  repo, and Pages cannot do HTTP Basic auth. Anything served is
 *                  world-readable.
 *   Vercel         can gate with edge middleware, but deploying requires an
 *                  interactive login that only Grant can complete.
 *
 * So the page is public and the DATA is not. The bundle is encrypted with a key
 * derived from a passphrase; the browser asks for it once and decrypts locally.
 * What GitHub serves is ciphertext.
 *
 * WHAT THIS IS AND IS NOT
 * AES-256-GCM with a 600,000-round PBKDF2-SHA256 key. That is real encryption,
 * not obfuscation -- there is no key in the page to find. But two things are
 * true and worth stating plainly:
 *
 *   1. The ciphertext is public. Anyone can download it and attack it offline,
 *      forever, at their own pace. The passphrase is the ONLY thing protecting
 *      it, so it needs to be a real passphrase and not a word.
 *   2. It is deployed as a Pages artifact and never committed, so there is no
 *      permanent git history of old ciphertext to attack. Only the current
 *      bundle is ever exposed.
 *
 * If Grant later connects Vercel, `site/middleware.js` gates it at the edge and
 * this step becomes unnecessary. This is the version that works today.
 *
 *   node tools/encrypt_bundle.mjs <in.json> <out.json>     # passphrase in RESEARCH_PASS
 */

import crypto from "node:crypto";
import fs from "node:fs";

const ITERATIONS = 600000;         // OWASP guidance for PBKDF2-SHA256
const SALT_BYTES = 16;
const IV_BYTES = 12;               // GCM standard
const KEY_BYTES = 32;              // AES-256

const [, , inPath, outPath] = process.argv;
if (!inPath || !outPath) {
  console.error("usage: node tools/encrypt_bundle.mjs <in.json> <out.json>");
  process.exit(2);
}

const pass = process.env.RESEARCH_PASS;
if (!pass) {
  // Fail closed. A missing passphrase must never silently publish plaintext --
  // that is the exact failure this whole file exists to prevent.
  console.error("RESEARCH_PASS is not set. Refusing to write an unencrypted bundle.");
  process.exit(1);
}
if (pass.length < 12) {
  console.error(
    `RESEARCH_PASS is ${pass.length} characters. The ciphertext is publicly ` +
    `downloadable and can be attacked offline forever, so a short passphrase ` +
    `is not protection. Use 12+ characters.`);
  process.exit(1);
}

const plaintext = fs.readFileSync(inPath);
const salt = crypto.randomBytes(SALT_BYTES);
const iv = crypto.randomBytes(IV_BYTES);
const key = crypto.pbkdf2Sync(pass, salt, ITERATIONS, KEY_BYTES, "sha256");

const cipher = crypto.createCipheriv("aes-256-gcm", key, iv);
const ct = Buffer.concat([cipher.update(plaintext), cipher.final()]);
const tag = cipher.getAuthTag();

fs.writeFileSync(outPath, JSON.stringify({
  v: 1,
  kdf: "PBKDF2-SHA256",
  iterations: ITERATIONS,
  salt: salt.toString("base64"),
  iv: iv.toString("base64"),
  // WebCrypto's decrypt expects the GCM tag appended to the ciphertext, so it
  // is concatenated here rather than shipped as a separate field.
  ct: Buffer.concat([ct, tag]).toString("base64"),
}));

const kb = (n) => (n / 1024).toFixed(0) + " KB";
console.log(`encrypted ${inPath} (${kb(plaintext.length)}) -> ${outPath} (${kb(fs.statSync(outPath).size)})`);
console.log(`  AES-256-GCM, PBKDF2-SHA256 x${ITERATIONS.toLocaleString()}`);

// Prove the round trip before anything is published. An unopenable bundle would
// look exactly like a wrong passphrase, and the wrong thing would get debugged.
const check = crypto.createDecipheriv("aes-256-gcm", key, iv);
check.setAuthTag(tag);
const back = Buffer.concat([check.update(ct), check.final()]);
if (!back.equals(plaintext)) {
  console.error("  ROUND-TRIP FAILED — refusing to publish");
  process.exit(1);
}
console.log("  round-trip verified");
