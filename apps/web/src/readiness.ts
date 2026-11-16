// Button title for GET /external-backups/readiness/{disk_id}. Pure (node:test).

export interface ReadinessLike {
  ready: boolean;
  reasons: { code: string; message: string }[];
}

/**
 * undefined when ready (no tooltip), the "checking" text while unknown,
 * otherwise every reason translated (English server message if a code is
 * unknown to this UI version).
 */
export function readinessTitle(
  readiness: ReadinessLike | undefined,
  texts: Record<string, string>,
): string | undefined {
  if (!readiness) return texts.checking;
  if (readiness.ready) return undefined;
  if (!readiness.reasons.length) return texts.unknown;
  return readiness.reasons.map((reason) => texts[reason.code] ?? reason.message).join("\n");
}
