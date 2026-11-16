import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import {
  browserLanguage,
  DEFAULT_LANGUAGE,
  initialLanguage,
  LANGUAGE_STORAGE_KEY,
  reconcileLanguage,
} from "../src/language.ts";

function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
  };
}

test("English is the default", () => {
  assert.equal(DEFAULT_LANGUAGE, "en");
  assert.equal(browserLanguage(["de-DE", "es"]), "en");
  assert.equal(browserLanguage([]), "en");
});

test("the browser language is only used on the very first load, then saved", () => {
  const storage = memoryStorage();
  assert.equal(initialLanguage(storage, ["fr-FR"]), "fr");
  assert.equal(storage.data.get(LANGUAGE_STORAGE_KEY), "fr");
  // Later loads: the saved choice, whatever the browser says.
  assert.equal(initialLanguage(storage, ["en-US"]), "fr");
});

test("a saved choice wins over the browser", () => {
  assert.equal(initialLanguage(memoryStorage({ [LANGUAGE_STORAGE_KEY]: "en" }), ["fr-FR"]), "en");
  assert.equal(initialLanguage(memoryStorage({ [LANGUAGE_STORAGE_KEY]: "xx" }), ["de"]), "en");
});

test("storage errors fall back to English without throwing", () => {
  const broken = {
    getItem: () => {
      throw new Error("denied");
    },
    setItem: () => {
      throw new Error("denied");
    },
  };
  assert.equal(initialLanguage(broken, ["it"]), "en");
  assert.equal(initialLanguage(null, undefined), "en");
});

test("after login the account preference wins; an account without one adopts the local choice", () => {
  assert.deepEqual(reconcileLanguage("fr", "en"), { language: "fr", pushToAccount: false });
  assert.deepEqual(reconcileLanguage(null, "fr"), { language: "fr", pushToAccount: true });
  assert.deepEqual(reconcileLanguage("zz", "en"), { language: "en", pushToAccount: true });
});

// --- No hard-coded UI text ------------------------------------------------------
// Every visible string of the wizard, the toasts and the login page comes from
// a translation table. JSX text nodes and aria-label/title literals are flagged.

const SRC = join(import.meta.dirname, "..", "src");
const ALLOWED_TEXT = new Set(["PBO", "×"]);

function tsxFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return tsxFiles(path);
    return path.endsWith(".tsx") ? [path] : [];
  });
}

// TypeScript between ">" and "<" (comparisons, generics, arrow functions) is not text.
const CODE_LIKE = /&&|\|\||=>|!==|===|\(event|ChangeEvent|\.length|\?\s*\(/;

function textOffenders(source: string): string[] {
  const clean = source.replace(/\{\/\*[\s\S]*?\*\/\}/g, "");
  const found: string[] = [];
  for (const match of clean.matchAll(/>\s*([^<>{}\n]*[A-Za-zÀ-ÿ][^<>{}\n]*?)\s*</g)) {
    const value = match[1].trim();
    if (!ALLOWED_TEXT.has(value) && !CODE_LIKE.test(value)) found.push(`>${value}<`);
  }
  for (const match of clean.matchAll(/\b(?:aria-label|title)="([^"]*[A-Za-z][^"]*)"/g)) found.push(match[0]);
  return found;
}

test("the hard-coded text detector works", () => {
  assert.deepEqual(textOffenders("<p>Waiting for a disk</p>"), [">Waiting for a disk<"]);
  assert.deepEqual(textOffenders('<ol aria-label="Setup steps">'), ['aria-label="Setup steps"']);
  assert.deepEqual(textOffenders("<p>{text.done.waiting}</p>{count > 0 && x.length > 1 ? (<b/>) : null}"), []);
  assert.deepEqual(textOffenders("<div>PBO</div>{/* a comment with <b>words</b> */}"), []);
});

test("no hard-coded text in the wizard, toasts and login page", () => {
  const files = [
    ...tsxFiles(join(SRC, "setup")),
    join(SRC, "components", "GlobalToasts.tsx"),
    join(SRC, "pages", "LoginPage.tsx"),
  ];
  const offenders = files.flatMap((file) => textOffenders(readFileSync(file, "utf8")).map((item) => `${file}: ${item}`));
  assert.deepEqual(offenders, []);
});
