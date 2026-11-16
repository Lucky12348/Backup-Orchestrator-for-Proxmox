import { useState } from "react";
import { ArrowRight, HardDrive, Unplug, Usb, WifiOff } from "lucide-react";

import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { cn } from "../../components/ui/utils";
import { applyDiskEvent, formatCapacity, rejectReasonText, type DiskCard } from "../../liveDisks";
import { useEvents } from "../../useEvents";
import type { WizardLanguage } from "../logic";
import { useText } from "../shared";

/**
 * Step 5 — live "plug a USB disk" block, subscribed to the SSE stream:
 * waiting state, then one card per detected disk (new / known / rejected
 * with the reason), turning "unplugged" on disk.removed.
 */
export function LiveDisks({ language, onExamine }: { language: WizardLanguage; onExamine: (diskId: number) => void }) {
  const text = useText();
  const [cards, setCards] = useState<DiskCard[]>([]);
  const connected = useEvents(["disk.added", "disk.removed", "disk.updated"], (event) =>
    setCards((current) => applyDiskEvent(current, event)),
  );

  return (
    <div className="flex flex-col gap-3" aria-live="polite">
      {cards.length === 0 ? (
        <div className="flex items-start gap-3 rounded-lg border border-accent/30 bg-accent/10 px-4 py-4">
          <span className="relative mt-0.5 flex h-5 w-5 items-center justify-center">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/30" />
            <Usb className="relative h-5 w-5 text-accent" />
          </span>
          <div className="flex flex-col gap-0.5">
            <p className="text-sm font-medium">{text.done.waiting}</p>
            <p className="text-[13px] text-muted">{text.done.plugDiskHint}</p>
          </div>
        </div>
      ) : (
        cards.map((card) => (
          <DiskCardView key={`${card.id}`} card={card} language={language} onExamine={() => onExamine(card.id)} />
        ))
      )}
      {!connected ? (
        <p className="flex items-center gap-1.5 text-[12px] text-faint">
          <WifiOff className="h-3.5 w-3.5" />
          {text.done.liveOffline}
        </p>
      ) : null}
    </div>
  );
}

function DiskCardView({ card, language, onExamine }: { card: DiskCard; language: WizardLanguage; onExamine: () => void }) {
  const text = useText();
  const rejected = card.status === "rejected";
  const statusLabel = card.unplugged ? text.done.status.unplugged : text.done.status[card.status];
  const variant = card.unplugged ? "neutral" : rejected ? "warning" : card.status === "new" ? "accent" : "success";
  return (
    <div
      key={card.revision}
      className={cn(
        "flex flex-col gap-3 rounded-lg border bg-bg px-4 py-3 animate-fade-in",
        rejected && !card.unplugged ? "border-warning/40" : "border-border",
        card.unplugged && "opacity-60",
      )}
    >
      <div className="flex items-start gap-3">
        <div
          className={cn(
            "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
            rejected ? "bg-warning/15 text-warning" : "bg-accent/15 text-accent",
          )}
        >
          {card.unplugged ? <Unplug className="h-4 w-4" /> : <HardDrive className="h-4 w-4" />}
        </div>
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-between gap-2">
            <p className="truncate text-sm font-medium">{card.title}</p>
            <Badge variant={variant}>{statusLabel}</Badge>
          </div>
          <p className="text-[13px] text-muted">
            {formatCapacity(card.capacityGb, language)} · {text.done.serial} <span className="font-mono">{card.serial}</span>
          </p>
          {rejected ? (
            <p className="mt-1 text-[13px] text-warning">{rejectReasonText(card.rejectReason, card.minSizeGb, language)}</p>
          ) : null}
        </div>
      </div>
      {!rejected && !card.unplugged ? (
        <Button size="sm" className="self-end" onClick={onExamine}>
          {text.done.examine}
          <ArrowRight />
        </Button>
      ) : null}
    </div>
  );
}
