import { useEffect, useState } from "react";

import { chooseSourceDatastore, getDatastores, type SourceDatastore } from "../../api";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../../components/ui/dialog";
import { cn } from "../../components/ui/utils";
import type { Language, TranslationDictionary } from "../../i18n";
import { useToast } from "../../ui/toast";
import { formatBytes } from "../../units";
import { fill } from "../format";
import { CloseButton } from "../resources/ProxmoxJobsSection";

/**
 * "Choose another datastore" (source deleted on PBS): the setting and the
 * plannings that used it switch to the chosen one (PUT /datastores/source).
 */
export function ChooseDatastoreDialog({
  open,
  missing,
  onClose,
  onChanged,
  language,
  t,
}: {
  open: boolean;
  missing: string | null;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
  language: Language;
  t: TranslationDictionary;
}) {
  const text = t.datastoresV2;
  const { toast } = useToast();
  const [items, setItems] = useState<SourceDatastore[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [choice, setChoice] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setItems(null);
    void getDatastores().then(
      (result) => {
        setItems(result.items);
        setError(result.error);
        setChoice(result.items[0]?.name ?? "");
      },
      (failure: unknown) => {
        setItems([]);
        setError(failure instanceof Error ? failure.message : t.error);
      },
    );
  }, [open, t.error]);

  if (!open) return null;

  async function save() {
    if (!choice) return;
    setBusy(true);
    try {
      await chooseSourceDatastore(choice);
      toast({ tone: "success", title: fill(text.chosen, { name: choice }) });
      await onChanged();
      onClose();
    } catch (failure) {
      toast({ tone: "danger", title: failure instanceof Error ? failure.message : t.error });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(value) => !value && onClose()}>
      <DialogContent className="max-w-[480px]" aria-describedby="choose-datastore-text">
        <div className="flex items-center gap-2 border-0 border-b border-solid border-border px-5 py-4">
          <DialogTitle className="m-0 flex-1 text-[17px] font-semibold">{text.chooseTitle}</DialogTitle>
          <CloseButton label={t.planningV2.closeDetail} onClick={onClose} />
        </div>
        <div className="flex flex-col gap-3 px-5 py-4">
          <DialogDescription id="choose-datastore-text" className="m-0 text-sm text-muted">
            {fill(text.chooseText, { name: missing ?? "—" })}
          </DialogDescription>
          {items === null ? <p className="m-0 text-[13px] text-faint">{t.integrationsV2.loading}</p> : null}
          {error ? <p className="m-0 text-xs text-danger-text">{error}</p> : null}
          <div role="radiogroup" aria-label={text.chooseTitle} className="flex flex-col gap-1.5">
            {(items ?? []).map((item) => (
              <label
                key={item.name}
                className={cn(
                  "flex cursor-pointer items-center gap-3 rounded-lg border border-solid px-3 py-2 text-sm",
                  choice === item.name ? "border-accent bg-accent/[0.07]" : "border-border",
                )}
              >
                <input type="radio" name="source-datastore" className="m-0" checked={choice === item.name} onChange={() => setChoice(item.name)} />
                <span className="flex-1 font-medium">{item.name}</span>
                {item.total ? (
                  <span className="text-xs text-faint">{fill(text.usage, { used: formatBytes(item.used ?? 0, language), total: formatBytes(item.total, language) })}</span>
                ) : null}
              </label>
            ))}
          </div>
        </div>
        <div className="flex justify-end gap-2 border-0 border-t border-solid border-border px-5 py-3.5">
          <button type="button" onClick={onClose} className="rounded-lg border border-solid border-border bg-subtle px-3.5 py-2 text-sm font-medium text-fg hover:brightness-110">
            {t.confirmDialogs.cancel}
          </button>
          <button type="button" disabled={busy || !choice} onClick={() => void save()} className="rounded-lg border-0 bg-accent px-3.5 py-2 text-sm font-medium text-white hover:brightness-110 disabled:opacity-60">
            {text.chooseAction}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
