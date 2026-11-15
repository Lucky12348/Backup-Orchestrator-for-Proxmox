# Audit avant version publique (Phase 0)

> Branche : `public-release` — état du code au commit `4a56c8c` (« fix eject disk logic »).
> Cible : une installation via community-scripts.org (1 LXC Debian natif sans Docker,
> config en base via un assistant, agent hôte installé en une ligne, UI Tailwind + shadcn/ui).
> Ce document est un **état des lieux** : aucun code métier n'a été modifié pour le produire.

Sommaire :
[A. Front](#a-inventaire-du-front) ·
[B. Endpoints API](#b-inventaire-des-endpoints-fastapi) ·
[C. Variables de config](#c-variables-de-configuration) ·
[D. Hypothèses d'infra en dur](#d-hypothèses-propres-à-linfra-actuelle-codées-en-dur) ·
[E. Agent PBS → API PBS ≥ 3.3](#e-actions-de-lagent-pbs-remplaçables-par-lapi-pbs--33) ·
[F. Sécurité](#f-points-de-sécurité) ·
[G. État build/tests/CI](#g-état-build--tests--ci-phase-0)

---

## A. Inventaire du front

Routage : [apps/web/src/App.tsx](../apps/web/src/App.tsx) (React Router v6, routes déclarées l. 266-323).
Pas de routeur pour la connexion : `AppInner` affiche `LoginPage` tant que non authentifié (l. 349-359).
Toutes les données des écrans principaux sont chargées **en bloc** par le hook
[useAppData.ts](../apps/web/src/hooks/useAppData.ts) (~20 appels `api.ts` au chargement + polling
`setInterval` l. 228) et passées en props ; seules Activity, Planning, Notifications et Settings
font leurs propres appels.

Toutes les confirmations destructives passent soit par la modale centrale `ConfirmModal`
(App.tsx, l. 326), soit par `window.confirm()` natif (à remplacer par un `AlertDialog` shadcn).

| Route | Écran / rôle | Composants | Appels `api.ts` | Actions destructives / sensibles |
|---|---|---|---|---|
| *(non auth)* | **LoginPage** — formulaire utilisateur/mot de passe | — | `POST /auth/token` via `AuthContext` (jeton en `sessionStorage`) | — |
| `/` | **DashboardPage** — synthèse : couverture PBS, dernier backup, état agents, disques | EmptyState, PageHeader, StatusBadge | aucun direct (données `useAppData` : `getOverview`, `getAgentStatus`, `getBackupRuns`, `getExternalBackupRuns`…) | aucune |
| `/assets` | **AssetsPage** — inventaire VM/CT Proxmox, couverture PBS, ignorer un asset, **CRUD des jobs de backup vzdump Proxmox** | DataTable, EmptyState, PageHeader, StatusBadge | via props : `updateAssetIgnore`, `updateProxmoxBackupJobSelection` ; direct : `createProxmoxBackupJob`, `replaceProxmoxBackupJob`, `deleteProxmoxBackupJob` | ⚠️ **suppression** d'un job vzdump sur le cluster (`window.confirm`, l. 266) ; modification de sélection de VM d'un job (`window.confirm`, l. 190) |
| `/disks` | **DisksPage** — disques externes détectés, confiance, mode dédié, lancement backup externe, éjection | DataTable, EmptyState, PageHeader, StatusBadge | via props : `updateDisk`, `getExternalBackupPreview` + `runExternalBackup`, `ejectExternalDisk` | ⚠️ **backup externe** (peut **formater** un disque non préparé — avertissement `externalBackupDestructiveWarning`) ; ⚠️ **éjection** (umount PBS + retrait passthrough USB) ; bascule `trusted` / `dedicated_backup_disk` (ConfirmModal « warning ») |
| `/planning` | **PlanningPage** — calendrier, événements planifiés (récurrence, fenêtre, auto-eject), runs à confirmer | DataTable, EmptyState, ErrorBanner, PageHeader, StatCard, StatusBadge | `getScheduledBackupEvents`, `getScheduledBackupCalendar`, `getScheduledBackupRuns`, `create/update/deleteScheduledBackupEvent`, `runScheduledBackupNow`, `confirmScheduledBackupRun`, `cancelScheduledBackupRun` | suppression d'événement (soft delete, `window.confirm` l. 201) ; **run-now** / **confirm** déclenchent un backup externe réel (passthrough + éventuel formatage) |
| `/integrations` | **IntegrationsPage** — état connexions Proxmox/PBS/agents, sync manuelle | PageHeader, StatusBadge | via props : `syncProxmoxInventory`, `syncPBSInventory` | aucune (sync en lecture, ConfirmModal « info ») |
| `/activity` | **ActivityPage** — historique runs externes (logs live), événements d'activité, retry eject | DataTable, EmptyState, PageHeader, StatusBadge | `getActivityEvents`, `getExternalBackupRun` ; via props : `cleanupExternalBackupRuns` + `cleanupBackupRuns`, `ejectExternalDisk` | ⚠️ **purge de l'historique** (garde les 10 derniers, ConfirmModal « danger ») ; retry d'éjection |
| `/notifications` | **NotificationsPage** — éditeur des templates du moteur ntfy **de l'hôte Proxmox** (via agent hôte), historique, file d'attente, test | DataTable, EmptyState, ErrorBanner, LoadingBlock, PageHeader, StatusBadge | `getHostNotifTemplates`, `saveHostNotifTemplates`, `testHostNotifEvent`, `getHostNotifHistory`, `getHostNotifQueue` | écrasement de `/etc/ntfy-notif/templates.json` sur l'hôte ; suppression d'un événement de template (`window.confirm` l. 163) |
| `/settings` | **SettingsPage** — préférences notifications app (ntfy), test, heure/version système, **mises à jour** app + agents | ErrorBanner, PageHeader, StatusBadge | `getNotificationStatus`, `get/update/resetNotificationPreferences`, `sendTestNotification`, `getSystemTime`, `getSystemVersion`, `getMaintenanceStatus`, `checkMaintenanceComponent`, `updateMaintenanceComponent` | ⚠️ **mise à jour + redémarrage** app/agents (`git pull` + `docker compose up --build` / restart systemd, `window.confirm` l. 250, 278) ; reset des préférences (l. 532) |

Composants partagés : `AppShell` (navigation, sélecteur de langue, horloge serveur via `getSystemTime`),
`ConfirmModal`, `DataTable`, `EmptyState`, `ErrorBanner`, `LoadingBlock`, `PageHeader`, `StatCard`, `StatusBadge`.

Code mort / chemins non câblés côté UI (à trancher avant la refonte) :

- `components/PrepareDiskModal.tsx` n'est importé nulle part, et `startDiskPreparation` exposé par
  `useAppData` n'est pas utilisé par `App.tsx` : **la préparation de disque n'est plus accessible
  depuis l'UI**, elle n'a lieu qu'implicitement pendant un backup externe.
- Fonctions de `api.ts` jamais appelées : `getAssetIgnores`, `getDisks`, `deleteExternalBackupRun`,
  `handoffDiskToPBS`, `detachDiskFromPBS`, `getDiskPBSVisibility`, `getDiskPreparationRuns`,
  `getDiskPreparationRun`, `updateAllMaintenanceComponents`.
- i18n maison ([i18n.ts](../apps/web/src/i18n.ts), FR/EN, ~900 lignes), langue par défaut `fr`
  (App.tsx l. 40 et 352 — la page de login est toujours en français).
- Styles : un seul `styles.css` (43 kB) sans framework — la migration Tailwind/shadcn est une réécriture
  de la couche présentation, pas un portage.

---

## B. Inventaire des endpoints FastAPI

Préfixe `/api/v1` sauf `/health`. Assemblage dans [router.py](../apps/api/app/api/router.py) :

- **JWT** = `protected_router`, dépendance `get_current_user` (Bearer JWT HS256, compte unique en env).
- **Agent** = public côté JWT mais protégé par en-tête `X-Agent-Token` (comparaison `secrets.compare_digest`).
- **Public** = aucune authentification.

| Méthode | Chemin | Auth | Fichier:ligne | Service appelé |
|---|---|---|---|---|
| GET | `/health` | Public | routes/health.py:7 | — |
| POST | `/auth/token` | Public (rate-limit mémoire par IP) | auth.py:164 | — (vérif bcrypt locale) |
| POST | `/agent/heartbeat` | Agent (HOST **ou** PBS token) | routes/agent.py:48 | `disks.record_agent_heartbeat` |
| POST | `/agent/disks/report` | Agent (HOST ou PBS token) | routes/agent.py:58 | `disks.ingest_agent_disk_report` |
| POST | `/external-backups/runs/{run_id}/log` | Agent (PBS token uniquement) | routes/external_backups.py:102 | `external_backups.append_external_backup_run_log` |
| GET | `/agent/status` | JWT | routes/agent.py:64 | `disks.get_agent_status`, `notifications.notify_agent_degraded` |
| GET | `/overview` | JWT | routes/overview.py:13 | `overview.get_overview_metrics` |
| GET | `/assets/ignore` | JWT | routes/assets.py:12 | `asset_ignores.list_asset_ignores` |
| PATCH | `/assets/{source}/{node}/{vmid}/ignore` | JWT | routes/assets.py:17 | `asset_ignores.upsert_asset_ignore` |
| GET | `/vms` | JWT | routes/vms.py:14 | `asset_ignores.get_asset_ignore_map` (requête ORM directe) |
| PATCH | `/vms/{vm_id}` | JWT | routes/vms.py:28 | ORM direct + `asset_ignores.vm_read_payload` |
| GET | `/backup-runs` | JWT | routes/backup_runs.py:14 | ORM direct |
| DELETE | `/backup-runs/cleanup` | JWT | routes/backup_runs.py:19 | `activity_cleanup.cleanup_backup_runs` |
| GET | `/disks` | JWT | routes/disks.py:29 | ORM direct |
| GET | `/disks/preferred` | JWT | routes/disks.py:34 | `disks.list_preferred_disks` |
| PATCH | `/disks/{disk_id}` | JWT | routes/disks.py:39 | ORM direct |
| POST | `/disks/{disk_id}/prepare` ⚠️ | JWT | routes/disks.py:67 | `disk_preparations.prepare_disk` → agent PBS |
| GET | `/disks/{disk_id}/preparation-runs` | JWT | routes/disks.py:83 | `disk_preparations.list_disk_preparation_runs` |
| DELETE | `/disks/preparation-runs/cleanup` | JWT | routes/disks.py:95 | `activity_cleanup.cleanup_disk_preparation_runs` |
| GET | `/disks/preparation-runs/{run_id}` | JWT | routes/disks.py:101 | `disk_preparations.get_disk_preparation_run` |
| POST | `/disks/{disk_id}/handoff-to-pbs` ⚠️ | JWT | routes/disks.py:107 | `disk_handoff.handoff_disk_to_pbs` → API PVE + agent hôte (`qm set`) |
| DELETE | `/disks/{disk_id}/handoff-to-pbs` ⚠️ | JWT | routes/disks.py:120 | `disk_handoff.detach_disk_from_pbs` → agent hôte |
| POST | `/disks/{disk_id}/eject` ⚠️ | JWT | routes/disks.py:129 | `disk_eject.eject_dedicated_external_disk` → agent PBS puis agent hôte |
| GET | `/disks/{disk_id}/pbs-visibility` | JWT | routes/disks.py:135 | `disk_handoff.get_pbs_disk_visibility` |
| GET | `/external-backups/preview/{disk_id}` | JWT | routes/external_backups.py:35 | `external_backups.get_external_backup_preview` |
| POST | `/external-backups/run` ⚠️ | JWT | routes/external_backups.py:40 | `external_backups.run_external_backup` (→ `external_backup_execution`) |
| GET | `/external-backups/runs` | JWT | routes/external_backups.py:57 | `external_backups.list_external_backup_runs` |
| DELETE | `/external-backups/runs/cleanup` | JWT | routes/external_backups.py:70 | `external_backups.cleanup_external_backup_runs` |
| POST | `/external-backups/legacy-objects/cleanup` | JWT | routes/external_backups.py:76 | `external_backups.cleanup_legacy_external_export_objects` → agent PBS |
| GET | `/external-backups/temporary-objects/status` | JWT | routes/external_backups.py:81 | `external_backups.inspect_external_export_objects` → agent PBS |
| POST | `/external-backups/temporary-objects/cleanup` | JWT | routes/external_backups.py:86 | `external_backups.cleanup_external_export_objects` → agent PBS |
| GET | `/external-backups/runs/{run_id}` | JWT | routes/external_backups.py:91 | `external_backups.get_external_backup_run` |
| DELETE | `/external-backups/runs/{run_id}` | JWT | routes/external_backups.py:97 | `external_backups.delete_external_backup_run` |
| GET | `/integrations/proxmox/status` | JWT | routes/integrations_proxmox.py:18 | `proxmox_client.ProxmoxClient`, `sync_state.get_proxmox_sync_state` |
| POST | `/integrations/proxmox/sync` | JWT | routes/integrations_proxmox.py:58 | `sync_state.run_proxmox_sync_guarded` |
| GET | `/integrations/proxmox/inventory` | JWT | routes/integrations_proxmox.py:85 | `asset_ignores.*` (ORM) |
| GET | `/integrations/pbs/status` | JWT | routes/integrations_pbs.py:25 | `pbs_client.PBSClient`, `sync_state.get_pbs_sync_state` |
| POST | `/integrations/pbs/sync` | JWT | routes/integrations_pbs.py:65 | `sync_state.run_pbs_sync_guarded` |
| GET | `/integrations/pbs/inventory` | JWT | routes/integrations_pbs.py:92 | `pbs_sync.list_pbs_inventory` |
| GET | `/proxmox/backup-jobs` | JWT | routes/proxmox.py:26 | `proxmox_client.ProxmoxClient` |
| PATCH | `/proxmox/backup-jobs/{job_id}/selection` ⚠️ | JWT | routes/proxmox.py:39 | `ProxmoxClient` + `sync_state.run_proxmox_sync_guarded` |
| POST | `/proxmox/backup-jobs` ⚠️ | JWT | routes/proxmox.py:75 | `ProxmoxClient` |
| PUT | `/proxmox/backup-jobs/{job_id}` ⚠️ | JWT | routes/proxmox.py:101 | `ProxmoxClient` |
| DELETE | `/proxmox/backup-jobs/{job_id}` ⚠️ | JWT | routes/proxmox.py:126 | `ProxmoxClient` |
| GET | `/planning/disks` | JWT | routes/planning.py:43 | `planning.get_disk_planning` |
| GET | `/planning/unplanned-assets` | JWT | routes/planning.py:48 | `planning.get_unplanned_assets` |
| GET | `/planning/overview` | JWT | routes/planning.py:62 | `planning.get_planning_overview` |
| GET | `/planning/events` | JWT | routes/planning.py:67 | ORM direct |
| GET | `/planning/calendar` | JWT | routes/planning.py:79 | `planning_scheduler.expand_occurrences` |
| POST | `/planning/events` | JWT | routes/planning.py:140 | ORM direct |
| GET | `/planning/events/{event_id}` | JWT | routes/planning.py:155 | ORM direct |
| PATCH | `/planning/events/{event_id}` | JWT | routes/planning.py:163 | ORM direct |
| DELETE | `/planning/events/{event_id}` | JWT | routes/planning.py:183 | ORM direct (soft delete) |
| GET | `/planning/runs` | JWT | routes/planning.py:209 | ORM direct |
| POST | `/planning/events/{event_id}/run-now` ⚠️ | JWT | routes/planning.py:215 | `planning_scheduler.run_event_now` |
| POST | `/planning/runs/{run_id}/confirm` ⚠️ | JWT | routes/planning.py:224 | `planning_scheduler.confirm_planning_run` |
| POST | `/planning/runs/{run_id}/cancel` | JWT | routes/planning.py:233 | `planning_scheduler.cancel_planning_run` |
| GET | `/system/time` | JWT | routes/system.py:19 | — |
| POST | `/system/auto-sync` | JWT | routes/system.py:33 | `sync_state.trigger_auto_sync_if_stale` |
| GET | `/system/version` | JWT | routes/system.py:43 | `host_agent.get_host_agent_client`, `get_pbs_agent_client` |
| GET | `/maintenance/updates/status` | JWT | routes/maintenance.py:31 | `maintenance.check_all_status` |
| POST | `/maintenance/updates/app/check` | JWT | routes/maintenance.py:36 | `maintenance.check_app_status` → app-maintenance-agent |
| POST | `/maintenance/updates/app/update` ⚠️ | JWT | routes/maintenance.py:41 | `maintenance.update_app` → app-maintenance-agent (`git pull` + compose) |
| POST | `/maintenance/updates/proxmox-agent/check` | JWT | routes/maintenance.py:46 | `maintenance.check_agent_status` |
| POST | `/maintenance/updates/proxmox-agent/update` ⚠️ | JWT | routes/maintenance.py:51 | `maintenance.update_agent` (agent hôte : `git pull` + restart) |
| POST | `/maintenance/updates/pbs-agent/check` | JWT | routes/maintenance.py:56 | `maintenance.check_agent_status` |
| POST | `/maintenance/updates/pbs-agent/update` ⚠️ | JWT | routes/maintenance.py:61 | `maintenance.update_agent` (agent PBS) |
| POST | `/maintenance/updates/update-all` ⚠️ | JWT | routes/maintenance.py:66 | `maintenance.update_all` |
| GET | `/notifications/status` | JWT | routes/notifications.py:22 | `notifications.get_notification_service` |
| GET | `/notifications/preferences` | JWT | routes/notifications.py:41 | `notifications.get_notification_preferences` |
| PATCH | `/notifications/preferences` | JWT | routes/notifications.py:46 | `notifications.update_notification_preferences` |
| POST | `/notifications/preferences/reset` | JWT | routes/notifications.py:52 | `notifications.reset_notification_preferences` |
| POST | `/notifications/test` | JWT | routes/notifications.py:57 | `notifications.get_notification_service` |
| GET | `/notifications/host/templates` | JWT | routes/host_notifications.py:25 | `host_notifications.get_templates` → agent hôte |
| PUT | `/notifications/host/templates` ⚠️ | JWT | routes/host_notifications.py:30 | `host_notifications.save_templates` → agent hôte |
| POST | `/notifications/host/test` | JWT | routes/host_notifications.py:36 | `host_notifications.send_test` → agent hôte |
| GET | `/notifications/host/history` | JWT | routes/host_notifications.py:49 | `host_notifications.get_history` → agent hôte |
| GET | `/notifications/host/queue` | JWT | routes/host_notifications.py:59 | `host_notifications.get_queue` → agent hôte |
| GET | `/activity-events` | JWT | routes/host_notifications.py:64 | `host_notifications.list_activity_events` |

⚠️ = action qui modifie l'infra (disque, VM, PBS, jobs Proxmox, code déployé).

**Endpoints de l'agent** (host et PBS, même binaire —
[server.py](../apps/agent/src/agent/server.py)), tous protégés par `X-Agent-Token`
(`require_agent_token`, l. 177, **y compris `/health`**) :
`GET /health` · `GET /version` · `GET /capabilities` · `POST /prepare-disk` ·
`POST /prepare-external-datastore` · `POST /prepare-dedicated-pbs-datastore` ·
`POST /eject-dedicated-pbs-datastore` · `POST /inspect-disk` · `POST /filesystem-usage` ·
`POST /qemu/usb/attach` · `POST /qemu/usb/detach` · `POST /qemu/config` · `POST /disk/spin-down` ·
`POST /run-external-export` · `POST /cleanup-legacy-external-export-objects` ·
`POST /external-export-objects/status` · `POST /external-export-objects/cleanup` ·
`POST /maintenance/check` · `POST /maintenance/update` ·
`GET|PUT /notifications/templates` · `POST /notifications/test` · `GET /notifications/history` ·
`GET /notifications/queue`.
Le binaire n'a **pas de notion de rôle** : l'agent hôte expose aussi les routes PBS et inversement.

---

## C. Variables de configuration

Légende : **[ENV]** garder en env système (bootstrap, secrets d'infra locaux) ·
**[DB]** migrer en base, éditable via l'assistant/UI · **[SUPPR]** supprimer (ou rendre inutile).

### C.1 API — [config.py](../apps/api/app/core/config.py) + [auth.py](../apps/api/app/auth.py) + notifications

| Variable | Défaut | Classement | Commentaire |
|---|---|---|---|
| `APP_ENV` | `development` | **[ENV]** | ⚠️ défaut dangereux : `development` déclenche `seed_database()` (main.py:23-24). Le défaut public doit être `production`. |
| `API_PORT` | 8000 | **[ENV]** | Lu mais **inutilisé** dans le code API (seul compose s'en sert). |
| `DATABASE_URL` | postgres@db | **[ENV]** | En LXC natif : Postgres local ou SQLite ; généré par le script d'install. |
| `AUTH_SECRET_KEY` | valeur insecure (refusée au démarrage) | **[ENV]** | Généré par l'installeur (`openssl rand`), jamais affiché dans l'UI. |
| `AUTH_TOKEN_EXPIRE_MINUTES` | 180 (max) | **[DB]** | Paramètre de session. |
| `AUTH_USERNAME` / `AUTH_PASSWORD_HASH` | `admin` / vide | **[DB]** | → table `users` créée par l'assistant au premier lancement. |
| `AUTH_ENABLED` | true | **[SUPPR]** | Permet de désactiver toute l'auth (`get_current_user` renvoie l'utilisateur sans jeton, auth.py:144). Inacceptable en public. |
| `FRONTEND_ORIGIN`, `FRONTEND_ORIGIN_ALT` | localhost:5173 | **[SUPPR]** | Inutiles si l'API sert le build statique en même origine. `FRONTEND_ORIGIN_ALT` n'est lu que via `cors_origins`. |
| `PVE_API_URL`, `PVE_API_TOKEN_ID`, `PVE_API_TOKEN_SECRET`, `PVE_VERIFY_SSL` | exemple.local / vide / false | **[DB]** | Étape « Proxmox » de l'assistant ; ajouter un champ empreinte TLS. Secret chiffré en base. |
| `PVE_NODE_NAME` | `proxmox` | **[DB]** | À découvrir via `GET /nodes` plutôt qu'à saisir. |
| `PBS_EXECUTION_VM_ID`, `PBS_EXECUTION_VM_NODE` | `100` / `PVE_NODE_NAME` | **[DB]** | À choisir dans une liste de VM découverte. |
| `PBS_API_URL`, `PBS_TOKEN_ID`, `PBS_TOKEN_SECRET`, `PBS_VERIFY_SSL` | exemple.local / vide / false | **[DB]** | Étape « PBS ». |
| `PBS_DATASTORE` | vide | **[DB]** | À choisir dans `GET /config/datastore`. |
| `HOST_AGENT_BASE_URL`, `HOST_AGENT_TOKEN`, `HOST_AGENT_TIMEOUT_SECONDS` | proxmox-host:8081 / vide / 7200 | **[DB]** | Le jeton devrait être **généré par l'app** et affiché dans la commande d'install une-ligne de l'agent. |
| `PBS_AGENT_BASE_URL`, `PBS_AGENT_TOKEN`, `PBS_AGENT_TIMEOUT_SECONDS` | pbs-host:8081 / vide / 7200 | **[DB]** | Idem. |
| `EXTERNAL_BACKUP_CALLBACK_BASE_URL` | `http://api:8000/api/v1` | **[DB]** | Nom d'hôte Docker en dur ; à dériver de l'URL publique saisie dans l'assistant. |
| `NOTIFICATIONS_ENABLED`, `NTFY_BASE_URL`, `NTFY_TOPIC`, `NTFY_USERNAME`, `NTFY_PASSWORD` | false / ntfy.sh / vide | **[DB]** | `notifications.py:166-175` relit aussi `os.getenv` directement — à unifier. |
| `NOTIFY_ON_*` (13 drapeaux) | true | **[DB]** | Déjà dupliqués dans la table `notification_preferences` (override) : l'env ne sert plus que de valeur par défaut → supprimer de l'env. |
| `LOW_COVERAGE_THRESHOLD_PERCENT`, `DISK_DETECTION_NOTIFY_COOLDOWN_SECONDS` | 100 / 1800 | **[DB]** | Déjà dans `notification_preferences`. |
| `PLANNING_SCHEDULER_ENABLED`, `PLANNING_SCHEDULER_INTERVAL_SECONDS` | true / 60 | **[ENV]** | Réglage d'exploitation, pas utilisateur. |
| `AUTO_SYNC_ENABLED`, `PROXMOX_SYNC_INTERVAL_SECONDS`, `PBS_SYNC_INTERVAL_SECONDS` | true / 60 / 60 | **[DB]** | |
| `AGENT_STALE_AFTER_MINUTES` | 10 | **[DB]** | |
| `AGENT_LOOP_DATASTORE_SIZE_GB` | 500 | **[DB]** | Ne concerne que le mode coexistence (legacy). |
| `EXTERNAL_BACKUP_STALE_WARNING_SECONDS` | 900 | **[SUPPR]** | Lu mais jamais utilisé. |
| `EXTERNAL_BACKUP_LEGACY_COEXISTENCE_ENABLED` | false | **[SUPPR]** | Mode legacy (loop image) — à retirer de la version publique ou à garder caché. |
| `SHOW_SEED_DISKS` | false | **[SUPPR]** | Avec le seed de démo. |
| `MAINTENANCE_TIMEOUT_SECONDS`, `APP_MAINTENANCE_AGENT_BASE_URL`, `APP_MAINTENANCE_AGENT_TOKEN` | 120 / 127.0.0.1:8092 / vide | **[SUPPR]** | Le flux « git pull + docker compose » est remplacé par la fonction `update` du script community-scripts. |
| `AGENT_REPO_PATH` (côté API) | cwd | **[SUPPR]** | Lu mais jamais utilisé dans l'API. |

### C.2 Agent hôte / PBS — [main.py](../apps/agent/src/agent/main.py) l. 80-121, [ntfy_notif.py](../apps/agent/src/agent/ntfy_notif.py) l. 88-92

L'agent n'a pas de base de données : « [DB] » signifie ici **poussé par l'API** (dans la requête
ou via un endpoint de configuration) au lieu d'être lu dans `/opt/.../.env`.

| Variable | Défaut | Classement | Commentaire |
|---|---|---|---|
| `AGENT_SERVER_TOKEN` | vide | **[ENV]** | Écrit par l'installeur une-ligne (jeton fourni par l'app). |
| `AGENT_SERVER_HOST`, `AGENT_SERVER_PORT` | `0.0.0.0` / **8081** | **[ENV]** | Le défaut code (8081) contredit les docs (8090/8091). |
| `AGENT_API_BASE_URL` | localhost:8000 | **[ENV]** | URL de l'app, renseignée par l'installeur. |
| `AGENT_HOSTNAME` | `socket.gethostname()` | **[ENV]** | |
| `AGENT_TIMEOUT_SECONDS`, `AGENT_EXPORT_TIMEOUT_SECONDS`, `AGENT_DATASTORE_CREATE_TIMEOUT_SECONDS`, `AGENT_EJECT_SYNC_TIMEOUT_SECONDS`, `AGENT_MAINTENANCE_TIMEOUT_SECONDS` | 10 / 7200 / 14400 / 1800 / 120 | **[ENV]** | Garder des défauts sains, surcharge optionnelle. |
| `AGENT_INCLUDE_NON_USB_CANDIDATES` | false | **[DB]** | Option avancée (élargit les disques éligibles — sensible). |
| `PBS_API_URL`, `PBS_TOKEN_ID`, `PBS_TOKEN_SECRET`, `PBS_FINGERPRINT` | vide | **[DB]** | Dupliqués avec l'API ; utilisés pour créer le *remote* PBS vers lui-même. Disparaissent si on passe à des sync jobs locaux (§E). |
| `PBS_DATASTORE` | vide | **[DB]** | Doit venir de l'API (déjà en base). |
| `AGENT_LOOP_DATASTORE_SIZE_GB` | 500 | **[SUPPR]** | Legacy coexistence. |
| `AGENT_VERSION` | `0.1.0` | **[SUPPR]** | À lire depuis les métadonnées du paquet. |
| `AGENT_REPO_PATH`, `AGENT_HTTP_SERVICE_NAME` | cwd / vide | **[SUPPR]** | Mise à jour par `git pull` remplacée par l'installeur/paquet (cf. incident documenté dans AGENTS.md). |
| `NTFY_NOTIF_TEMPLATES_PATH`, `NTFY_NOTIF_BACKUPS_DIR`, `NTFY_NOTIF_HISTORY_PATH`, `NTFY_NOTIF_QUEUE_DIR`, `NTFY_NOTIF_SEND_COMMAND` | `/etc/ntfy-notif/...`, `/usr/local/bin/ntfy-send` | **[SUPPR]** (ou module optionnel) | Pilote un moteur de notification **propre à ton hôte** et absent du dépôt (voir §D). |

### C.3 App maintenance agent — [main.py](../apps/app-maintenance-agent/src/app_maintenance_agent/main.py) l. 25-30

`APP_MAINTENANCE_AGENT_HOST/PORT/TOKEN`, `APP_REPO_PATH`, `APP_COMPOSE_FILE`,
`APP_MAINTENANCE_TIMEOUT_SECONDS`, `APP_MAINTENANCE_AGENT_SERVICE` → **[SUPPR]** : composant entier
lié au déploiement Docker Compose ; sans Docker, il n'a plus de raison d'être.

### C.4 Front / Compose

| Variable | Classement | Commentaire |
|---|---|---|
| `WEB_PORT`, `WEB_ALLOWED_HOSTS`, `API_PROXY_TARGET` | **[SUPPR]** | Ne concernent que le serveur de dev Vite, aujourd'hui utilisé **en production** (web.Dockerfile : `npm run dev`). |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | **[ENV]** → générés | Si Postgres est conservé dans le LXC ; sinon SQLite. |

---

## D. Hypothèses propres à l'infra actuelle codées en dur

| # | Hypothèse | Fichier:ligne | Impact public |
|---|---|---|---|
| D1 | **PBS tourne dans une VM du même cluster PVE** et reçoit les disques par passthrough USB QEMU | `services/disk_handoff.py` (tout le module), `config.py:63-64` | Exclut PBS bare-metal, PBS en LXC, PBS distant. Architecture centrale du produit. |
| D2 | VMID PBS par défaut **100** | `apps/api/app/core/config.py:63` | Doit être choisi explicitement. |
| D3 | **Nœud unique** : un seul `PVE_NODE_NAME` (défaut `proxmox`) pour lister les USB et piloter la VM | `config.py:62`, `config.py:64`, `disk_handoff.py:51` | Disque branché sur un autre nœud que la VM PBS = invisible ; migration de la VM PBS casse le passthrough. |
| D4 | Passthrough USB par `host=<bus>-<port>` ou `vendor:product` via `qm set` sur l'hôte (root) | `apps/agent/src/agent/main.py:727-752`, `server.py:85-86`, `disk_handoff.py:276-345` | Requiert un agent root sur l'hôte PVE. |
| D5 | Slots USB libres cherchés dans `usb0..usb4` uniquement | `disk_handoff.py:672-674` | Limite arbitraire (PVE en supporte plus selon la version de machine). |
| D6 | Chemin de montage `/mnt/pbo/<serial>/pbs-datastore` | `services/external_backups.py:34`, `services/disk_eject.py:63`, `services/disk_preparation_agent.py:28`, agent `main.py:1957`, `:2598`, `:2827` | À aligner sur `/mnt/datastore/<name>` des removable datastores PBS (§E). |
| D7 | Écriture d'une entrée **`/etc/fstab`** (`defaults,nofail`) pour chaque disque préparé | agent `main.py:1964-1974` | Le fstab de la VM PBS accumule des disques amovibles ; inutile avec les removable datastores. |
| D8 | Racine datastores PBS `/mnt/datastore` (garde-fou de formatage) | agent `main.py:39` | OK, mais à garder cohérent avec D6. |
| D9 | Export = création d'un **remote PBS vers lui-même** (`PBS_API_URL` + token) + sync job temporaire `pbo-export-*` | agent `main.py:989-992`, `:1085-1127`, `:2156` | Le secret du token passe en argument de commande (`--password`, l. 1097), visible dans `ps`. |
| D10 | Propriétaire du sync job = utilisateur du token (`--owner <user>`) | agent `main.py:1125-1126` | Dépend du schéma de token choisi. |
| D11 | Propriété `backup:backup` pour les datastores | agent `main.py:306`, `:1922` | Valide pour PBS standard. |
| D12 | **Moteur ntfy maison sur l'hôte** : `/etc/ntfy-notif/templates.json`, `/usr/local/bin/ntfy-send`, `/var/spool/ntfy-queue`, `/var/log/ntfy-notif/history.jsonl`, groupe propriétaire **`nut`** | agent `ntfy_notif.py:5-9`, `:51`, `:88-92` ; UI `i18n.ts:278`, `hostNotifCatalog.ts` (événements `auth.ssh_ok`, IP `192.168.1.50`…) | Le script `ntfy-send` n'est pas dans le dépôt : toute la page Notifications est inutilisable ailleurs. |
| D13 | Noms d'hôtes/IP personnels dans le serveur Vite (`extbackup.sofianechaoui.fr`, `192.168.1.103`) | `apps/web/vite.config.ts:5-8` | Fuite d'info + inutile en prod. |
| D14 | Défaut `PBS_DATASTORE=backup` dans Compose (contredit le commit « remove default name datastore ») | `infra/docker/docker-compose.yml:31` | Incohérence avec `config.py:74` (vide par défaut). |
| D15 | Hôtes Docker en dur : `http://api:8000/api/v1`, `host.docker.internal:8092`, `db:5432` | `config.py:28`, `config.py:83`, `docker-compose.yml:12,14,35` | Disparaît avec la sortie de Docker. |
| D16 | Chemins d'installation `/opt/proxmox-backup-orchestrator-agent` et `/opt/proxmox_backup_orchestrator` (deux conventions différentes) | `apps/agent/deploy/systemd/*.service`, `apps/app-maintenance-agent/deploy/systemd/*.service:9-11`, app-maint `main.py:28` | À normaliser dans l'installeur. |
| D17 | Fuseau horaire par défaut **`Europe/Paris`** pour la planification | `models/scheduled_backup.py:49`, `schemas/planning.py:45` | À prendre du navigateur / de l'assistant. |
| D18 | Langue par défaut **`fr`**, login toujours en FR | `apps/web/src/App.tsx:40`, `:352` | Détecter `navigator.language`. |
| D19 | Mises à jour par `git pull --ff-only` sur un clone + `docker compose up --build` | agent `main.py:3174-3242`, app-maint `main.py:29,126` | Remplacé par le mécanisme `update` de community-scripts. |
| D20 | Frontend servi par `vite dev` en production | `infra/docker/web.Dockerfile` (`CMD npm run dev`) | Servir `dist/` statique (FastAPI `StaticFiles` ou nginx/caddy). |
| D21 | Port d'écoute agent par défaut 8081 vs 8090/8091 dans `.env.example`/docs | agent `main.py:112`, `config.py:75,78`, `.env.example:97,102` | Source d'erreurs d'installation. |

---

## E. Actions de l'agent PBS remplaçables par l'API PBS ≥ 3.3

PBS 3.3 a introduit les **removable datastores** (datastore lié à un `backing-device` par UUID de
système de fichiers, monté/démonté par PBS lui-même), et rendu le *remote* facultatif pour les sync
jobs (synchronisation locale datastore → datastore). Cela recouvre l'essentiel de ce que fait
l'agent PBS via `proxmox-backup-manager` + `mount`/`umount`.

> Les chemins ci-dessous sont des **candidats** à valider contre l'apidoc (`/api2/json` sur une PBS
> 3.3/3.4/4.x de test) avant toute implémentation — noms de paramètres et privilèges requis compris.

| Action actuelle (agent PBS) | Code actuel | Endpoint API PBS candidat | Risque |
|---|---|---|---|
| Lister les disques visibles / inspecter | `lsblk`, `blkid`, `udevadm` (`main.py:1943`, `:2085`) | `GET /nodes/{node}/disks/list` | Faible. Détail moins riche que lsblk (pas de mountpoints partition) → garder `inspect-disk` côté agent en lecture si nécessaire. |
| Effacer + partitionner + mkfs ext4 + monter + fstab + créer datastore | `wipefs`, `sgdisk`, `parted`, `partprobe`, `mkfs.ext4`, `mount`, fstab, `datastore create` (`main.py:473-530`, `:1964`) | `PUT /nodes/{node}/disks/wipedisk` puis `POST /nodes/{node}/disks/directory` (`filesystem=ext4`, `add-datastore=1`, `removable-datastore=1`) | **Élevé** (destructif). Perte des garde-fous maison (`_assert_disk_hosts_no_pbs_datastore`, marqueur PBO, taille ≥ 32 Go, refus si datastore existant) → **les réimplémenter côté API avant l'appel**. |
| Réutiliser un disque déjà préparé | `datastore create … --reuse-datastore true` (`main.py:1903`) | `POST /config/datastore` (`name`, `backing-device=<uuid>`, `reuse-datastore=1`) | Moyen : migration des disques existants (montés via fstab sous `/mnt/pbo/...`) vers des removable datastores → procédure de migration à écrire et tester. |
| Monter le datastore | `mount <part> <path>` | `POST /admin/datastore/{store}/mount` | Faible. Chemin imposé `/mnt/datastore/<store>` (cf. D6). |
| Éjection sûre : vérifier tâches en cours, `sync -f`, `umount`, gestion « busy » (stop/start services) | `main.py:568-660`, `:2718-2751` | `GET /nodes/{node}/tasks?running=1&store=…` puis `POST /admin/datastore/{store}/unmount` (PBS passe le store en maintenance « unmount » et attend la fin des tâches) | Moyen : le correctif récent (flush long sur USB lent, `EjectNotFinishedError`) doit être revalidé avec le démontage natif ; garder le polling de la tâche (UPID) jusqu'au statut final avant de retirer le passthrough. |
| Créer remote vers soi-même + sync job temporaire + run + cleanup | `remote create`, `sync-job create/run/remove`, `remote remove` (`main.py:1085-1181`) | `POST /config/sync` **sans remote** (sync local), `POST /admin/sync/{id}/run`, `GET /nodes/{node}/tasks/{upid}/status`, `DELETE /config/sync/{id}` — ou job **permanent** par disque, lancé à la demande | Faible-moyen : supprime le token passé en ligne de commande (D9) et le parsing de logs (`pbs_progress.py`). Vérifier la version minimale du sync local et le filtrage (namespaces, groupes, `remove-vanished`). |
| Lister / nettoyer les objets temporaires `pbo-export-*` | `main.py:2570-2589`, `:2942-3060` | `GET /config/sync`, `GET /config/remote`, `DELETE …` | Faible. Devient presque inutile avec des jobs permanents. |
| Lister datastores / détecter un datastore sur un disque | `datastore list --output-format json` (`main.py:976`, `:1668`, `:1878`) | `GET /config/datastore`, `GET /admin/datastore/{store}/status` | Faible. |
| Statut d'utilisation du FS | `POST /filesystem-usage` (statvfs) | `GET /admin/datastore/{store}/status` (total/used/avail) | Faible. |
| Passthrough USB vers la VM PBS (agent **hôte**, `qm set`) | `main.py:727-752` | **API PVE** : `PUT /nodes/{node}/qemu/{vmid}/config` avec `usbN=mapping=<id>` (resource mappings PVE ≥ 8, privilège `Mapping.Use`) ; création des mappings via `/cluster/mapping/usb` | Moyen : le passthrough `host=` brut exige root@pam, d'où l'agent hôte. Les mappings permettent un token non-root, mais doivent être créés par disque/port. |
| Spin-down du disque (agent hôte) | `POST /disk/spin-down` | Aucun | Reste dans l'agent hôte. |

Bilan : l'agent **PBS** pourrait disparaître (tout passe par l'API PBS avec un token dédié) ; l'agent
**hôte** se réduirait à la découverte des disques USB, au spin-down et (si pas de mappings) au `qm set`.
Contre-partie : **PBS ≥ 3.3 devient un prérequis** et il faut un chemin de migration pour les disques
déjà préparés « à l'ancienne ».

---

## F. Points de sécurité

| Sujet | Constat | Recommandation |
|---|---|---|
| Ports agents | Agents en HTTP clair, écoute `0.0.0.0` par défaut (`main.py:111`), port défaut 8081 ≠ docs. Seule protection : pare-feu + jeton. | Écouter sur l'IP de management seulement ; installeur qui pose une règle `pve-firewall`/nftables limitée à l'IP du LXC. |
| TLS | Agents sans TLS ; `PVE_VERIFY_SSL`/`PBS_VERIFY_SSL` à `false` par défaut ; callbacks de log en HTTP. Le jeton `X-Agent-Token` circule en clair. | TLS sur les agents (cert auto-signé généré à l'install + **épinglage d'empreinte** côté app). Pour PVE/PBS : épinglage d'empreinte (TOFU dans l'assistant) au lieu de `verify=false`. |
| Jetons agents | Jeton statique partagé, **réutilisé dans les deux sens** : `PBS_AGENT_TOKEN` authentifie l'app→agent *et* sert de `callback_token` agent→app (`external_backup_agent.py:225`) ; `/agent/heartbeat` accepte indifféremment le jeton hôte ou PBS. Pas de rotation. | Un jeton par sens et par agent, généré par l'app, rotation depuis l'UI, stocké chiffré. Associer heartbeat ↔ agent attendu. |
| Rôle des agents | Le même binaire expose toutes les routes (formatage, `qm set`, export…) quelle que soit la machine. | Variable `AGENT_ROLE=host|pbs` qui n'active que les routes du rôle. |
| Privilèges root | Agents `User=root` (systemd), exécutent `wipefs`, `sgdisk`, `parted`, `mkfs`, `qm`, `git pull`. Un jeton volé = root sur l'hôte PVE. | Durcissement systemd (`ProtectSystem`, `CapabilityBoundingSet`, `NoNewPrivileges` là où c'est possible) ; supprimer `git pull` (D19) ; à terme remplacer l'agent PBS par l'API (§E) et l'agent hôte par des resource mappings. |
| Secrets en ligne de commande | `remote create --password <secret>` (agent `main.py:1097`) visible via `ps`/journald pendant l'export. | Disparaît avec les sync jobs locaux ; sinon passer par l'API PBS. |
| Auth app | Compte unique en env ; `AUTH_ENABLED=false` désactive tout (auth.py:144) ; rate-limit de login en mémoire (perdu au redémarrage, par processus) ; JWT HS256 en `sessionStorage` (exposé à un XSS). | Supprimer `AUTH_ENABLED` ; table `users` + assistant ; cookie `HttpOnly; Secure; SameSite=Strict` ; CSP stricte avec le build statique. |
| Seed de démo | `seed_database()` dès que `APP_ENV != production`, et le défaut est `development` (main.py:23-24). | Défaut `production` ; seed derrière un flag explicite de dev. |
| Serveur de dev en prod | Vite dev server exposé (web.Dockerfile). | Servir `dist/` en statique derrière l'API ou un reverse proxy. |
| Endpoint maintenance | L'UI peut déclencher `git pull` + rebuild + restart sur 3 machines. | Retirer ; mises à jour via community-scripts (`update`) et paquet agent. |
| Template ntfy hôte | L'app peut écrire `/etc/ntfy-notif/templates.json` sur l'hôte PVE via l'agent root. | Module optionnel désactivé par défaut, ou retiré de la version publique. |
| CORS | `allow_credentials=True` + `allow_methods/headers=*` sur deux origines configurables. | Inutile en même origine : supprimer. |
| Base de données | Postgres exposé sur `127.0.0.1:5432` avec mot de passe env. | En LXC : socket Unix / `listen_addresses=localhost`, mot de passe généré. |

---

## G. État build / tests / CI (Phase 0)

Mesuré le 2026-11-15 sous Windows (Node 24.11, Python 3.14) :

| Vérification | Résultat |
|---|---|
| `npm run build` (apps/web : `tsc --noEmit && vite build`) | ✅ OK — bundle JS 344 kB (101 kB gzip), CSS 43 kB |
| `npm test` (nouveau script : `node --test "tests/*.test.ts"`) | ✅ 15/15 (diskPlanning 3, ejectRetry 3, hostNotifCatalog 9) |
| `pytest` apps/api | ✅ 124 passés, ~100 `DeprecationWarning` (`datetime.utcnow()`) |
| `pytest` apps/agent | ✅ 111 passés (+20 sous-tests) |
| `pytest` apps/app-maintenance-agent | ✅ 3 passés |
| `ruff --select E9,F63,F7,F82` (erreurs fatales) | ✅ aucune |
| `ruff` règles par défaut (`E4,E7,E9,F`) | ⚠️ 4 × F401 imports inutilisés : `schemas/__init__.py:16`, `schemas/integrations_proxmox.py:5`, `services/planning_scheduler.py:5`, `tests/test_planning_events.py:5` — non corrigés (Phase 0 = pas de changement de code) |

Changements Phase 0 :

- `apps/web/package.json` : script `test`, `engines.node >= 22.18` (exécution native des `.ts` par `node --test`).
- `apps/web/package-lock.json` : ajouté (requis pour `npm ci` reproductible en CI).
- `.github/workflows/ci.yml` : jobs Python (api, agent, app-maintenance-agent × Python 3.11/3.13 :
  ruff bloquant sur erreurs fatales + rapport non bloquant des règles par défaut, pytest) et web
  (Node 22 : `npm ci`, build, tests). Pas encore exécuté sur GitHub (aucun push).

Notes : `AGENTS.md` est en retard sur l'inventaire des tests (il annonce 10 fichiers API / 1 agent /
1 web ; il y en a 15 / 5 / 3) et sur l'absence de CI — à mettre à jour quand la CI sera poussée.
