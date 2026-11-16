// B26: what the legacy global banners said ("External backup run started",
// errors of an action…) is now a toast. useAppData queues notices, the shell
// turns each one into a toast (NoticeToasts). Pure module (node:test).

export type NoticeTone = "success" | "danger" | "info";

export interface Notice {
  id: number;
  tone: NoticeTone;
  /** Untranslated text (API message, legacy French/English strings). */
  message: string;
  /** Stable API code (readiness, disk decision, rescan…) translated by the UI. */
  code?: string;
}

/** Error raised by api.ts: HTTP status + the API `detail.code` when there is one. */
export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

let nextId = 0;

export function makeNotice(tone: NoticeTone, message: string, code?: string): Notice {
  nextId += 1;
  return { id: nextId, tone, message, code };
}

export function noticeFromError(error: unknown): Notice {
  if (error instanceof ApiError) return makeNotice("danger", error.message, error.code);
  return makeNotice("danger", error instanceof Error ? error.message : "Unknown error");
}

/** Translated title of a notice: the code's text when the UI knows it. */
export function noticeTitle(notice: Notice, tables: Array<Record<string, string> | undefined>): string {
  if (notice.code) {
    for (const table of tables) {
      const text = table?.[notice.code];
      if (text) return text;
    }
  }
  return notice.message;
}
