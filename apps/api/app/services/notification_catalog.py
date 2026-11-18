"""Every event PBO can notify, its default level, how long an identical alert
is folded into the previous one, the screen it links to, and its short
message in English and French (Phase 5j, docs/NOTIFICATIONS.md).

Messages carry the disk's name and colour (an emoji dot, readable in every
channel). `{placeholders}` are filled by the emitter; a missing one is left
empty rather than failing a notification.

B55: every message follows PBO's language, its parameters too. A parameter
that is a word or a sentence (a run step, a SMART state, an agent role, why a
run stopped) is a `term()` — {"en": …, "fr": …} — picked when rendering; free
text from PVE/PBS/agents (English) never goes into a message.
"""

from __future__ import annotations

import string
from dataclasses import dataclass

LEVELS = ("info", "warning", "error")
LEVEL_RANK = {level: index for index, level in enumerate(LEVELS)}

# Disk colours of the disk sheet (diskProfile.COLOR_CLASS) as emoji dots.
COLOR_DOT = {"slate": "⚪", "blue": "🔵", "green": "🟢", "amber": "🟠", "red": "🔴", "violet": "🟣"}


@dataclass(frozen=True)
class EventSpec:
    key: str
    group: str
    level: str
    # Identical alerts (same dedup key) inside this window are folded into one.
    dedup_hours: float
    link: str
    title: dict[str, str]
    body: dict[str, str]


def _event(key: str, group: str, level: str, dedup_hours: float, link: str, en: tuple[str, str], fr: tuple[str, str]) -> EventSpec:
    return EventSpec(key, group, level, dedup_hours, link, {"en": en[0], "fr": fr[0]}, {"en": en[1], "fr": fr[1]})


EVENTS: tuple[EventSpec, ...] = (
    # Runs
    _event("run_success", "runs", "info", 0, "/disks/{disk_id}",
           ("Backup done", "{disk}: copy finished in {detail}."),
           ("Sauvegarde terminée", "{disk} : copie terminée en {detail}.")),
    _event("run_failed", "runs", "error", 0, "/disks/{disk_id}",
           ("{kind} failed", "{disk}: {detail}. Details in the run journal."),
           ("{kind} en échec", "{disk} : {detail}. Détails dans le journal de l’exécution.")),
    _event("run_interrupted", "runs", "error", 0, "/disks/{disk_id}",
           ("{kind} interrupted", "{disk}: {detail}"),
           ("{kind} interrompue", "{disk} : {detail}")),
    _event("verify_failed", "runs", "error", 0, "/disks/{disk_id}",
           ("Verification failed", "{disk}: damaged versions found. Repair them from the disk sheet."),
           ("Vérification en échec", "{disk} : des versions sont endommagées. Réparez-les depuis la fiche du disque.")),
    _event("repair_completed", "runs", "info", 0, "/disks/{disk_id}",
           ("Repair finished", "{disk}: the damaged versions were copied again and verified."),
           ("Réparation terminée", "{disk} : les versions endommagées ont été recopiées et vérifiées.")),
    # Disks and rotation
    _event("unsafe_removal", "disks", "warning", 6, "/disks/{disk_id}",
           ("Disk removed without eject", "{disk}: verify the copy once it is plugged back in."),
           ("Disque retiré sans éjection", "{disk} : vérifiez la copie au prochain branchement.")),
    _event("disk_to_plug", "disks", "info", 12, "/planning",
           ("Plug in the disk", "{disk} is expected for “{planning}” at {time}."),
           ("Disque à brancher", "{disk} est attendu pour « {planning} » à {time}.")),
    _event("rotation_missed", "disks", "warning", 12, "/planning",
           ("Rotation missed", "“{planning}”: {disk} was not plugged in."),
           ("Rotation manquée", "« {planning} » : {disk} n’était pas branché.")),
    _event("backup_stale", "disks", "warning", 24, "/disks/{disk_id}",
           ("No backup for {days} days", "{disk}: last backup {days} days ago."),
           ("Aucune sauvegarde depuis {days} jours", "{disk} : dernière sauvegarde il y a {days} jours.")),
    _event("disk_space", "disks", "warning", 24, "/disks/{disk_id}",
           ("Disk {percent} % full", "{disk}: {used} GiB used of {total} GiB."),
           ("Disque plein à {percent} %", "{disk} : {used} Gio utilisés sur {total} Gio.")),
    _event("smart_degraded", "disks", "warning", 24, "/disks/{disk_id}",
           ("Disk health degraded", "{disk}: SMART {status}. Check the disk sheet."),
           ("Santé du disque dégradée", "{disk} : SMART {status}. Consultez la fiche du disque.")),
    _event("disk_new", "disks", "info", 1, "/disks/{disk_id}",
           ("New disk detected", "{disk} is waiting for your decision."),
           ("Nouveau disque détecté", "{disk} attend votre décision.")),
    # Agents
    _event("agent_offline", "agents", "error", 6, "/integrations",
           ("Agent offline", "The {role} agent ({host}) no longer answers."),
           ("Agent hors ligne", "L’agent {role} ({host}) ne répond plus.")),
    _event("agent_update_failed", "agents", "warning", 12, "/integrations",
           ("Agent update failed", "{role} agent ({host}): {detail}"),
           ("Mise à jour d’agent en échec", "Agent {role} ({host}) : {detail}")),
    # Integrations
    _event("pve_unreachable", "integrations", "error", 6, "/integrations",
           ("Proxmox VE unreachable", "PBO can no longer reach Proxmox VE{host}. Check the connection in Integrations."),
           ("Proxmox VE injoignable", "PBO ne parvient plus à joindre Proxmox VE{host}. Vérifiez la connexion dans Intégrations.")),
    _event("pbs_unreachable", "integrations", "error", 6, "/integrations",
           ("Proxmox Backup Server unreachable", "PBO can no longer reach Proxmox Backup Server{host}. Check the connection in Integrations."),
           ("Proxmox Backup Server injoignable", "PBO ne parvient plus à joindre Proxmox Backup Server{host}. Vérifiez la connexion dans Intégrations.")),
    _event("guest_agent_missing", "integrations", "warning", 24, "/integrations",
           ("PBS guest agent missing", "Stale mounts cannot be checked on the PBS VM: install qemu-guest-agent."),
           ("Guest agent PBS absent", "Les montages obsolètes ne peuvent pas être vérifiés sur la VM PBS : installez qemu-guest-agent.")),
)

EVENTS_BY_KEY: dict[str, EventSpec] = {event.key: event for event in EVENTS}
EVENT_KEYS: tuple[str, ...] = tuple(EVENTS_BY_KEY)
GROUPS: tuple[str, ...] = ("runs", "disks", "agents", "integrations")

TEST_MESSAGE = {
    "en": ("PBO test notification", "This channel works: PBO can reach it."),
    "fr": ("Notification de test PBO", "Ce canal fonctionne : PBO peut l’atteindre."),
}
DIGEST_TITLE = {"en": "PBO daily summary ({count})", "fr": "Résumé quotidien PBO ({count})"}

STALE_DISK = {"en": "a disk", "fr": "un disque"}


def term(en: str, fr: str) -> dict[str, str]:
    """A parameter in both languages, picked by render()."""
    return {"en": en, "fr": fr}


# Parameter values, in both languages (B55).
RUN_KINDS = {
    "backup": term("Backup", "Sauvegarde"),
    "verify": term("Verification", "Vérification"),
    "repair": term("Repair", "Réparation"),
}
STOP_REASONS = {
    "cancelled": term("cancelled by the operator. The disk was released; the versions already copied are kept.",
                      "interrompue par l’utilisateur. Le disque a été libéré ; les versions déjà copiées sont conservées."),
    "cancelled_not_released": term("cancelled by the operator, but the disk could not be released from the PBS VM. Eject it from its sheet.",
                                   "interrompue par l’utilisateur, mais le disque n’a pas pu être libéré de la VM PBS. Éjectez-le depuis sa fiche."),
    "during_sync": term("unplugged during the copy. The copy is incomplete: the next backup starts with a verification.",
                        "débranché pendant la copie. La copie est incomplète : la prochaine sauvegarde commence par une vérification."),
    "after_sync": term("unplugged before its datastore was unmounted. A verification is required.",
                       "débranché avant le démontage de son datastore. Une vérification est nécessaire."),
}
FAILED_STAGES = {
    "passthrough": term("failed while handing the disk to the PBS VM", "échec pendant le passage du disque à la VM PBS"),
    "mount": term("failed while preparing or mounting its datastore", "échec pendant la préparation ou le montage de son datastore"),
    "sync": term("failed during the copy", "échec pendant la copie"),
    "cleanup": term("failed during the cleanup (retention)", "échec pendant le nettoyage (rétention)"),
    "verify": term("damaged versions found by the verification", "la vérification a trouvé des versions endommagées"),
    "release": term("failed while releasing the disk", "échec pendant la libération du disque"),
    "readiness": term("could not start (the disk or a service was not ready)", "n’a pas pu démarrer (disque ou service pas prêt)"),
    "unknown": term("failed", "échec"),
}
SMART_STATES = {
    "warning": term("warning", "avertissement"),
    "danger": term("critical", "critique"),
}
AGENT_ROLES = {
    "host": term("Proxmox host", "de l’hôte Proxmox"),
    "pbs": term("PBS", "PBS"),
}
UPDATE_RESULTS = {
    "rolled_back": term("the new version did not start; the previous one was restored", "la nouvelle version n’a pas démarré ; la précédente a été restaurée"),
    "failed": term("the update failed; the agent keeps its current version", "la mise à jour a échoué ; l’agent garde sa version actuelle"),
    "too_old": term("too old to update itself: run its install command once more", "trop ancien pour se mettre à jour : relancez sa commande d’installation"),
    "cancelled": term("the update was cancelled", "la mise à jour a été annulée"),
}


class _Lenient(dict):
    def __missing__(self, key: str) -> str:  # unknown placeholder → empty, never a KeyError
        return ""


def _value(value: object, language: str) -> object:
    if isinstance(value, dict) and "en" in value:  # a term()
        return value.get(language) or value["en"]
    return "" if value is None else value


def render(template: str, params: dict[str, object], language: str = "en") -> str:
    text = string.Formatter().vformat(template, (), _Lenient({k: _value(v, language) for k, v in params.items()}))
    return " ".join(text.split())  # also drops the space a missing value leaves


def disk_param(name: str | None, color: str | None) -> str | dict[str, str]:
    """« 🔵 Rotation A »: the disk's colour as a dot, then its name (a term
    when the disk has no name)."""
    dot = COLOR_DOT.get(color or "")
    label = (name or "").strip()
    if not label:
        return {lang: f"{dot} {text}" if dot else text for lang, text in STALE_DISK.items()}
    return f"{dot} {label}" if dot else label


def disk_label(name: str | None, color: str | None, language: str = "en") -> str:
    value = disk_param(name, color)
    return value if isinstance(value, str) else value.get(language, value["en"])


def message(event: str, language: str, params: dict[str, object]) -> tuple[str, str, str]:
    """(title, body, link path) of an event in a language ("en" / "fr")."""
    spec = EVENTS_BY_KEY[event]
    lang = language if language in ("en", "fr") else "en"
    values = {"kind": RUN_KINDS["backup"], **params}
    return render(spec.title[lang], values, lang), render(spec.body[lang], values, lang), link_path(event, values)


def link_path(event: str, params: dict[str, object]) -> str:
    path = render(EVENTS_BY_KEY[event].link, params)
    return path.rstrip("/") or "/" if path.endswith("/") else path  # "/disks/{disk_id}" without a disk


def level_allows(level: str, minimum: str) -> bool:
    return LEVEL_RANK.get(level, 0) >= LEVEL_RANK.get(minimum, 0)
