# community-scripts drafts (not tested)

Drafts for a submission to [community-scripts](https://community-scripts.org/).
New scripts go to **DevScripts** first (`community-scripts/DevScripts`, see its
CONTRIBUTING.md); maintainers then promote them to ProxmoxVE.

| File here | Target in DevScripts |
|---|---|
| `ct/pbo.sh` | `ct/pbo.sh` |
| `install/pbo-install.sh` | `install/pbo-install.sh` |
| `frontend/public/json/pbo.json` | `json/pbo.json` (DevScripts keeps one JSON per script under `json/`; metadata ends up on the website) |

Based on the conventions observed on 2026-11-15 (`ct/paperless-ngx.sh`,
`install/paperless-ngx-install.sh`, `json/gopeed.json`). Helper signatures
(`check_for_gh_release`, `fetch_and_deploy_gh_release`, `setup_postgresql`,
`setup_postgresql_db`) were taken from their call sites and the
community-scripts/core `lib/*.func` headers, not exercised.

To check before submitting:

- `categories`: id from DevScripts `json/metadata.json` (7 assumed for
  "Backup & Recovery" — verify).
- `logo`: a dedicated icon (selfh.st) instead of the generic Proxmox one.
- The release asset `pbo-<version>.tar.gz` (see `.github/workflows/release.yml`)
  must exist: `fetch_and_deploy_gh_release ... "prebuild"` downloads it.
- `update_script` mirrors `scripts/install/pbo-update.sh` but without its
  automatic rollback; the reference installer stays `scripts/install/`.
