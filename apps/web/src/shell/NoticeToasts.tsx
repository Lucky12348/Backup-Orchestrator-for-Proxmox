import { useEffect, useRef } from "react";

import type { TranslationDictionary } from "../i18n";
import { noticeTitle, type Notice } from "../notices";
import { useToast } from "../ui/toast";

// API codes the UI can translate (readiness reasons, disk decision, rescan).
const codeTables = (t: TranslationDictionary): Array<Record<string, string>> => [t.readiness, t.disksV2.errors];

/** B26: the notices of useAppData (former global banners) become toasts. */
export function NoticeToasts({ notices, onShown, t }: { notices: Notice[]; onShown: (id: number) => void; t: TranslationDictionary }) {
  const { toast } = useToast();
  const shown = useRef(new Set<number>());
  useEffect(() => {
    for (const notice of notices) {
      if (shown.current.has(notice.id)) continue;
      shown.current.add(notice.id);
      toast({
        tone: notice.tone,
        title: noticeTitle(notice, codeTables(t)),
      });
      onShown(notice.id);
    }
  }, [notices, onShown, t, toast]);
  return null;
}
