# TSN-EXAMPLE

Template for a new TSN. Replace this file (and everything under the directory)
with content for your CVE / mitigation bundle.

## Required edits

1. Rename the directory to `TSN-<id>` (e.g. `TSN-CVE-2026-99999`).
2. Update `playbook.yml` — change the play `name:` to your TSN id.
3. Fill in `vars/main.yml` with CVE-specific values.
4. Replace `pre_tasks/detect.yml` with real read-only detection (tagged
   `[apply, rollback]` only).
5. Implement `roles/install/tasks/main.yml` (tag `apply`), capturing pre-change
   state into `{{ tsn_marker_dir }}/<name>.orig` for each thing you mutate.
6. Implement `roles/rollback/tasks/main.yml` (tag `rollback`) using only the
   markers written by install.
7. Implement `roles/validate/tasks/main.yml`:
   - Informational tasks tagged `[apply, rollback]`.
   - Strict assertions tagged `[validate]`.
8. Write a real README.md for the TSN.
