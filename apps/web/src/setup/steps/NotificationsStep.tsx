import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Bell, Loader2 } from "lucide-react";

import { createNotificationChannel, getNotificationCatalog, type NotificationCatalog } from "../../api";
import { Button } from "../../components/ui/button";
import { CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../../components/ui/card";
import { translations } from "../../i18n";
import type { UiLanguage } from "../../language";
import { ChannelForm, apiErrorCode } from "../../views/notifications/ChannelForm";
import { draftErrors, emptyDraft, toPayload, type ChannelDraft } from "../../views/notifications/notificationsModel";
import { useText } from "../shared";

/**
 * Optional step between PBS and Done (Phase 5j): add a first notification
 * channel and test it, or "Later" (Notifications page at any time). Every
 * event is selected; the page refines events, level, quiet hours.
 */
export function NotificationsStep({ language, onBack, onNext }: { language: UiLanguage; onBack: () => void; onNext: () => void }) {
  const text = useText();
  const t = translations[language];
  const [catalog, setCatalog] = useState<NotificationCatalog | null>(null);
  const [draft, setDraft] = useState<ChannelDraft | null>(null);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getNotificationCatalog().then(
      (value) => {
        setCatalog(value);
        setDraft({ ...emptyDraft("ntfy", value.events.map((event) => event.key)), name: t.notificationsV2.kinds.ntfy });
      },
      () => setCatalog(null),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function addAndContinue() {
    if (!draft) return;
    setSaving(true);
    setServerErrors({});
    try {
      await createNotificationChannel(toPayload(draft));
      onNext();
    } catch (error) {
      const { code, field } = apiErrorCode(error);
      setServerErrors({ [field ?? "name"]: code });
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <CardHeader>
        <Bell className="mb-1 h-4 w-4 text-muted" />
        <CardTitle>{text.notifications.title}</CardTitle>
        <CardDescription>{text.notifications.description}</CardDescription>
      </CardHeader>
      <CardContent>
        {catalog && draft ? (
          <ChannelForm draft={draft} onChange={setDraft} catalog={catalog} t={t} serverErrors={serverErrors} compact />
        ) : (
          <Loader2 className="h-4 w-4 animate-spin text-muted" />
        )}
      </CardContent>
      <CardFooter>
        <Button variant="ghost" onClick={onBack}>
          <ArrowLeft />
          {text.back}
        </Button>
        <span className="flex-1" />
        <Button variant="secondary" onClick={onNext} data-later>
          {text.notifications.later}
        </Button>
        <Button disabled={!draft || saving || Object.keys(draftErrors(draft)).length > 0} onClick={() => void addAndContinue()} data-add-channel>
          {saving ? <Loader2 className="animate-spin" /> : null}
          {text.notifications.add}
          <ArrowRight />
        </Button>
      </CardFooter>
    </>
  );
}
