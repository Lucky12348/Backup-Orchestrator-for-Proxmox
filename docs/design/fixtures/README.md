# Run log fixtures

Used by `apps/api/tests/test_run_log.py` (log cleanup B22/B23).

| File | Origin |
|---|---|
| `backup-reuse.stdout.log` | **Real** stdout of a backup on pve-test (2026-11-16, datastore reused, auto-eject), copied from the Activity page. |
| `eject.stdout.log` | **Real** stdout of the eject run that followed it. |
| `first-backup-format.stdout.log` | **Reconstructed**: first backup with formatting. The real log of that run was not available; the PBO lines come from the code, the PBS lines (`Wiping`, `Chunkstore create: n%`, `Summary: sync job pulled 352.682 MiB … 64.208 MiB/s`) follow the PBS task output described in the test report. Replace it with the real export when one is available — the tests read the file, not its exact content. |
