import { useState, type FormEvent } from "react";
import { useAuth } from "../AuthContext";
import type { TranslationDictionary } from "../i18n";

interface SetupPageProps {
  t: TranslationDictionary;
}

const MIN_PASSWORD_LENGTH = 12;

/**
 * First launch: no account exists yet, create the administrator.
 * Proxmox/PBS connection steps use /api/v1/setup/{proxmox,pbs} once logged in
 * (their UI comes with the Tailwind/shadcn rewrite).
 */
export function SetupPage({ t }: SetupPageProps) {
  const { createAdmin } = useAuth();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;
  const mismatch = confirmation.length > 0 && confirmation !== password;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (password.length < MIN_PASSWORD_LENGTH || password !== confirmation) return;
    setError(null);
    setLoading(true);
    try {
      await createAdmin(username.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Account creation failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-shell">
      <div className="login-bg" />
      <div className="login-bg-grid" />

      <div className="login-card">
        <div className="login-header">
          <div className="login-logo">PBO</div>
          <h1 className="login-title">{t.title}</h1>
          <p className="login-subtitle">PREMIER LANCEMENT — CRÉER LE COMPTE ADMINISTRATEUR</p>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          {error && <div className="login-error">{error}</div>}
          {tooShort && <div className="login-error">Au moins {MIN_PASSWORD_LENGTH} caractères.</div>}
          {mismatch && <div className="login-error">Les mots de passe ne correspondent pas.</div>}

          <div className="login-field">
            <label htmlFor="pbo-setup-username">USERNAME</label>
            <input
              id="pbo-setup-username"
              type="text"
              autoCapitalize="none"
              autoComplete="username"
              autoCorrect="off"
              spellCheck={false}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              minLength={3}
              maxLength={64}
              disabled={loading}
            />
          </div>

          <div className="login-field">
            <label htmlFor="pbo-setup-password">PASSWORD</label>
            <input
              id="pbo-setup-password"
              type="password"
              autoComplete="new-password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={MIN_PASSWORD_LENGTH}
              disabled={loading}
            />
          </div>

          <div className="login-field">
            <label htmlFor="pbo-setup-confirm">CONFIRM PASSWORD</label>
            <input
              id="pbo-setup-confirm"
              type="password"
              autoComplete="new-password"
              value={confirmation}
              onChange={(e) => setConfirmation(e.target.value)}
              required
              disabled={loading}
            />
          </div>

          <button
            className="login-submit"
            type="submit"
            disabled={loading || !username || password.length < MIN_PASSWORD_LENGTH || password !== confirmation}
          >
            {loading ? "CREATING..." : "CREATE ACCOUNT ->"}
          </button>
        </form>
      </div>
    </div>
  );
}
