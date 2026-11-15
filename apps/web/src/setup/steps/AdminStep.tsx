import { useState, type FormEvent } from "react";
import { ArrowRight, Loader2 } from "lucide-react";

import { useAuth } from "../../AuthContext";
import { Button } from "../../components/ui/button";
import { CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../../components/ui/card";
import { ErrorAlert, Field, useText } from "../shared";

const MIN_PASSWORD_LENGTH = 12;

/** Step 1 — replaces the former SetupPage. */
export function AdminStep() {
  const text = useText();
  const { createAdmin } = useAuth();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;
  const mismatch = confirmation.length > 0 && confirmation !== password;
  const valid = username.trim().length >= 3 && password.length >= MIN_PASSWORD_LENGTH && password === confirmation;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    try {
      await createAdmin(username.trim(), password);
    } catch (err) {
      setError(err); // coded error ({code, message}) from AuthContext
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      <CardHeader>
        <CardTitle>{text.admin.title}</CardTitle>
        <CardDescription>{text.admin.description}</CardDescription>
      </CardHeader>
      <CardContent>
        <ErrorAlert error={error} />
        <Field
          label={text.admin.username}
          autoCapitalize="none"
          autoComplete="username"
          spellCheck={false}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          minLength={3}
          maxLength={64}
          required
          disabled={busy}
        />
        <Field
          label={text.admin.password}
          type="password"
          autoComplete="new-password"
          autoFocus
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          hint={tooShort ? <span className="text-warning">{text.admin.tooShort(MIN_PASSWORD_LENGTH)}</span> : text.admin.tooShort(MIN_PASSWORD_LENGTH)}
          required
          disabled={busy}
        />
        <Field
          label={text.admin.confirm}
          type="password"
          autoComplete="new-password"
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
          hint={mismatch ? <span className="text-warning">{text.admin.mismatch}</span> : undefined}
          required
          disabled={busy}
        />
      </CardContent>
      <CardFooter className="justify-end">
        <Button type="submit" disabled={busy || !valid}>
          {busy ? <Loader2 className="animate-spin" /> : null}
          {busy ? text.admin.submitting : text.admin.submit}
          {busy ? null : <ArrowRight />}
        </Button>
      </CardFooter>
    </form>
  );
}
