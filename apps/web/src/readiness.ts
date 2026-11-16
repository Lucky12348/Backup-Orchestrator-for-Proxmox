// Button title for GET /external-backups/readiness/{disk_id}. Pure (node:test).

export interface ReadinessLike {
  ready: boolean;
  reasons: { code: string; message: string; [key: string]: unknown }[];
}

/** A reason's text, its {params} filled (numbers with the language's decimal mark). */
export function reasonText(
  reason: { code: string; message: string; [key: string]: unknown },
  texts: Record<string, string>,
  lang: "fr" | "en" = "en",
): string {
  const template = texts[reason.code];
  if (!template) return reason.message;
  return template.replace(/\{(\w+)\}/g, (whole, key: string) => {
    const value = reason[key];
    if (typeof value === "number") return lang === "fr" ? String(value).replace(".", ",") : String(value);
    return typeof value === "string" ? value : whole;
  });
}

/**
 * undefined when ready (no tooltip), the "checking" text while unknown,
 * otherwise every reason translated (English server message if a code is
 * unknown to this UI version).
 */
export function readinessTitle(
  readiness: ReadinessLike | undefined,
  texts: Record<string, string>,
  lang: "fr" | "en" = "en",
): string | undefined {
  if (!readiness) return texts.checking;
  if (readiness.ready) return undefined;
  if (!readiness.reasons.length) return texts.unknown;
  return readiness.reasons.map((reason) => reasonText(reason, texts, lang)).join("\n");
}
