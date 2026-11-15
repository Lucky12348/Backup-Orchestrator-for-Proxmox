import { useEffect, useState } from "react";

import {
  createAgentPairing,
  getRegisteredAgents,
  revokeRegisteredAgent,
  type AgentPairing,
  type RegisteredAgent,
} from "../api";
import type { Language } from "../i18n";
import { formatDateTime } from "../utils";
import { StatusBadge } from "./StatusBadge";

// Minimal pairing UI (Phase 3). The full agents screen comes with the UI rebuild.
const TEXT = {
  en: {
    title: "Agent installation",
    intro: "Generate a single-use code (valid 15 minutes), then run these two commands as root on the Proxmox VE host.",
    generate: "Generate install command",
    expires: "Expires",
    select: "Click the block to select both lines.",
    fingerprint: "App certificate fingerprint (compare it with the one the script prints)",
    agents: "Registered agents",
    none: "No agent registered yet.",
    revoke: "Revoke",
    active: "active",
    revoked: "revoked",
    lastSeen: "Last seen",
  },
  fr: {
    title: "Installation de l'agent",
    intro: "Générez un code à usage unique (valable 15 minutes), puis lancez ces deux commandes en root sur l'hôte Proxmox VE.",
    generate: "Générer la commande d'installation",
    expires: "Expire",
    select: "Cliquez dans le bloc pour sélectionner les deux lignes.",
    fingerprint: "Empreinte du certificat de l'app (à comparer avec celle affichée par le script)",
    agents: "Agents enregistrés",
    none: "Aucun agent enregistré.",
    revoke: "Révoquer",
    active: "actif",
    revoked: "révoqué",
    lastSeen: "Vu pour la dernière fois",
  },
} as const;

export function AgentPairingCard({ language, notAvailable }: { language: Language; notAvailable: string }) {
  const text = TEXT[language === "fr" ? "fr" : "en"];
  const [pairing, setPairing] = useState<AgentPairing | null>(null);
  const [agents, setAgents] = useState<RegisteredAgent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () =>
    getRegisteredAgents()
      .then(setAgents)
      .catch((err: Error) => setError(err.message));

  useEffect(() => {
    void refresh();
  }, []);

  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      setPairing(await createAgentPairing());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (agentId: number) => {
    try {
      await revokeRegisteredAgent(agentId);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <article className="panel-card">
      <div className="panel-card-header">
        <h2>{text.title}</h2>
        <button className="action-button" disabled={busy} onClick={generate} type="button">
          {text.generate}
        </button>
      </div>
      <div className="integration-details">
        <p className="integration-message">{text.intro}</p>
        {pairing ? (
          <>
            {/* No "Copy" button on purpose: writing `curl … | bash`-style commands to the
                clipboard is flagged as ClickFix by content blockers (uBlock Origin).
                The block selects as a whole on click for a manual copy. */}
            <pre className="log-pre" style={{ userSelect: "all", whiteSpace: "pre-wrap" }}>
              {pairing.install_commands.join("\n")}
            </pre>
            <p className="integration-message">
              {text.select} {text.expires} : {formatDateTime(pairing.expires_at, language, notAvailable)}
            </p>
            {pairing.app_fingerprint ? (
              <p className="integration-message">
                {text.fingerprint} :<br />
                <code>{pairing.app_fingerprint}</code>
              </p>
            ) : null}
          </>
        ) : null}
        {error ? <p className="integration-message danger-text">{error}</p> : null}
        <h3>{text.agents}</h3>
        {agents.length === 0 ? <p className="integration-message">{text.none}</p> : null}
        {agents.map((agent) => (
          <div className="summary-row" key={agent.id}>
            <span>
              <strong>{agent.hostname}</strong> ({agent.role}) — {agent.base_url}
              <br />
              {text.lastSeen} : {formatDateTime(agent.last_seen_at, language, notAvailable)}
            </span>
            <span>
              <StatusBadge tone={agent.active ? "success" : "neutral"}>{agent.active ? text.active : text.revoked}</StatusBadge>{" "}
              {agent.active ? (
                <button className="action-button" onClick={() => void revoke(agent.id)} type="button">
                  {text.revoke}
                </button>
              ) : null}
            </span>
          </div>
        ))}
      </div>
    </article>
  );
}
