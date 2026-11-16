import { ArrowLeft, ArrowRight, Bell } from "lucide-react";

import { Button } from "../../components/ui/button";
import { CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../../components/ui/card";
import { useText } from "../shared";

/**
 * Optional step between PBS and Done — PREPARED, NOT ENABLED: rendered only
 * when NOTIFICATIONS_STEP_ENABLED (setup/logic.ts) is switched on. The form
 * (ntfy URL, topic, test message) comes with that change.
 */
export function NotificationsStep({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  const text = useText();
  return (
    <>
      <CardHeader>
        <div className="mb-1 flex h-10 w-10 items-center justify-center rounded-full bg-accent/15 text-accent">
          <Bell className="h-5 w-5" />
        </div>
        <CardTitle>{text.notifications.title}</CardTitle>
        <CardDescription>{text.notifications.description}</CardDescription>
      </CardHeader>
      <CardContent />
      <CardFooter>
        <Button variant="ghost" onClick={onBack}>
          <ArrowLeft />
          {text.back}
        </Button>
        <Button variant="secondary" onClick={onNext}>
          {text.notifications.skip}
          <ArrowRight />
        </Button>
      </CardFooter>
    </>
  );
}
