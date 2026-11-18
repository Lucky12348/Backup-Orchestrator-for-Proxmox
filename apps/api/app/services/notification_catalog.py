"""Every event PBO can notify, its default level, how long an identical alert
is folded into the previous one, the screen it links to, and its short
message in English and French (Phase 5j, docs/NOTIFICATIONS.md).

Messages carry the disk's name and colour (an emoji dot, readable in every
channel). `{placeholders}` are filled by the emitter; a missing one is left
empty rather than failing a notification.
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
           ("Backup failed", "{disk}: {detail}"),
           ("Sauvegarde en échec", "{disk} : {detail}")),
    _event("run_interrupted", "runs", "error", 0, "/disks/{disk_id}",
           ("Backup interrupted", "{disk} was unplugged during the run. The copy must be verified."),
           ("Sauvegarde interrompue", "{disk} a été débranché pendant l’exécution. La copie doit être vérifiée.")),
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
           ("Disk health degraded", "{disk}: SMART {status}. {detail}"),
           ("Santé du disque dégradée", "{disk} : SMART {status}. {detail}")),
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
           ("Proxmox VE unreachable", "{detail}"),
           ("Proxmox VE injoignable", "{detail}")),
    _event("pbs_unreachable", "integrations", "error", 6, "/integrations",
           ("Proxmox Backup Server unreachable", "{detail}"),
           ("Proxmox Backup Server injoignable", "{detail}")),
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


class _Lenient(dict):
    def __missing__(self, key: str) -> str:  # unknown placeholder → empty, never a KeyError
        return ""


def render(template: str, params: dict[str, object]) -> str:
    text = string.Formatter().vformat(template, (), _Lenient({k: "" if v is None else v for k, v in params.items()}))
    return " ".join(text.split())  # also drops the space a missing value leaves


def disk_label(name: str | None, color: str | None, language: str = "en") -> str:
    """« 🔵 Rotation A »: the disk's colour as a dot, then its name."""
    label = (name or "").strip() or STALE_DISK.get(language, STALE_DISK["en"])
    dot = COLOR_DOT.get(color or "")
    return f"{dot} {label}" if dot else label


def message(event: str, language: str, params: dict[str, object]) -> tuple[str, str, str]:
    """(title, body, link path) of an event in a language ("en" / "fr")."""
    spec = EVENTS_BY_KEY[event]
    lang = language if language in ("en", "fr") else "en"
    return render(spec.title[lang], params), render(spec.body[lang], params), render(spec.link, params)


def level_allows(level: str, minimum: str) -> bool:
    return LEVEL_RANK.get(level, 0) >= LEVEL_RANK.get(minimum, 0)
