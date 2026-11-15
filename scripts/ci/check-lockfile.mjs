#!/usr/bin/env node
// Fails when apps/web/package-lock.json cannot drive a reproducible `npm ci`
// on Linux (CI, Docker alpine, Debian LXC):
//   * every installed package needs `resolved` and `integrity`;
//   * the Linux x64 native binaries of esbuild and rollup (glibc AND musl) must
//     be listed, otherwise a lock generated on Windows/macOS only carries that
//     platform's optional binaries and `vite build` breaks on Linux.
//
// Usage: node scripts/ci/check-lockfile.mjs [path/to/package-lock.json]
// Fix:   rm -rf node_modules package-lock.json && npm install   (npm >= 10,
//        `npm config get omit-lockfile-registry-resolved` must be false)

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const lockPath = resolve(process.argv[2] ?? "apps/web/package-lock.json");
const REQUIRED_OPTIONAL = [
  "@esbuild/linux-x64",
  "@rollup/rollup-linux-x64-gnu",
  "@rollup/rollup-linux-x64-musl",
];

let lock;
try {
  lock = JSON.parse(readFileSync(lockPath, "utf8"));
} catch (error) {
  console.error(`check-lockfile: cannot read ${lockPath}: ${error.message}`);
  process.exit(1);
}

const errors = [];
if (!lock.packages || typeof lock.packages !== "object") {
  errors.push(`lockfileVersion ${lock.lockfileVersion}: no "packages" section (npm >= 7 lockfile required)`);
} else {
  for (const [key, entry] of Object.entries(lock.packages)) {
    if (key === "" || entry.link) continue; // root project, workspace links
    const missing = ["resolved", "integrity"].filter((field) => !entry[field]);
    if (missing.length) errors.push(`${key}: missing ${missing.join(" + ")}`);
  }
  for (const name of REQUIRED_OPTIONAL) {
    if (!lock.packages[`node_modules/${name}`]) {
      errors.push(`node_modules/${name}: Linux binary not in the lockfile`);
    }
  }
}

if (errors.length) {
  console.error(`check-lockfile: ${lockPath} is not usable for npm ci on Linux:`);
  for (const line of errors.slice(0, 50)) console.error(`  - ${line}`);
  if (errors.length > 50) console.error(`  … and ${errors.length - 50} more`);
  console.error("Regenerate it: rm -rf node_modules package-lock.json && npm install (npm >= 10).");
  process.exit(1);
}
console.log(`check-lockfile: ${lockPath} OK (${Object.keys(lock.packages).length - 1} packages).`);
