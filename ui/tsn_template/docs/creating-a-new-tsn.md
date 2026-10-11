# Creating a new TSN

A TSN is a self-contained directory under `tsns/` that exposes three operations
on the same playbook via tags: `apply`, `rollback`, `validate`.

## 1. Scaffold

```powershell
.\template\scripts\new-tsn.ps1 -Name TSN-CVE-2026-99999
```

This copies `template/TSN-EXAMPLE/` to `tsns/TSN-CVE-2026-99999/` and rewrites
the play `name:` in `playbook.yml`. The resulting layout:

```
tsns/TSN-CVE-2026-99999/
├── README.md
├── playbook.yml
├── vars/main.yml
├── pre_tasks/detect.yml
└── roles/
    ├── install/{tasks/main.yml,files/}
    ├── rollback/tasks/main.yml
    └── validate/tasks/main.yml
```

## 2. Fill in the contract

### `vars/main.yml`
TSN-specific variables only — CVE IDs, kernel params, file paths, marker names.
Framework-wide defaults (e.g. `tsn_marker_dir`) live in `group_vars/all.yml`.

### `pre_tasks/detect.yml`
Read-only host detection shared by `install` and `rollback`. Every task here is
tagged `[apply, rollback]`. Do not put anything mutating here. The `validate`
role re-detects what it needs and never reads from this file.

### `roles/install/tasks/main.yml`
- Every task tagged `[apply]`.
- For every change you make, first persist the pre-change value into
  `{{ tsn_marker_dir }}/<name>.orig` (idempotently — only on first run; never
  overwrite an existing marker, otherwise re-applying erases the true original).
- Static files (e.g. modprobe drop-ins) live in `roles/install/files/` and are
  pushed with `ansible.builtin.copy: src: <filename>`.

### `roles/rollback/tasks/main.yml`
- Every task tagged `[rollback]`.
- Only act on what is recorded in markers. If a marker is absent (e.g. apply was
  skipped on this host or never ran), leave the system alone.
- After restoring, remove the marker.

### `roles/validate/tasks/main.yml`
Two clearly labeled sections:
1. **Informational** (tags `[apply, rollback]`) — `debug` / `shell` tasks that
   print pass/fail messages but never fail the play.
2. **Strict** (tag `[validate]`) — `ansible.builtin.assert` blocks that exit
   non-zero when the mitigation is not in effect. Use these in CI.

## 3. Run

```bash
ansible-playbook -i inventory/hosts.ini \
    tsns/TSN-CVE-2026-99999/playbook.yml --tags apply
ansible-playbook -i inventory/hosts.ini \
    tsns/TSN-CVE-2026-99999/playbook.yml --tags validate
ansible-playbook -i inventory/hosts.ini \
    tsns/TSN-CVE-2026-99999/playbook.yml --tags rollback
```

## Conventions checklist

- [ ] Every task carries one of `apply`, `rollback`, `validate` — no untagged
      task in any role.
- [ ] `install` writes a marker for every value it mutates.
- [ ] `rollback` reads markers and does nothing when they are missing.
- [ ] `validate` has both informational and strict sections.
- [ ] `pre_tasks/detect.yml` is read-only and tagged `[apply, rollback]`.
- [ ] No `become:` on individual tasks (the play already runs with `become: true`).
- [ ] No `gather_facts: true` unless the role actually needs facts.
- [ ] `README.md` documents the CVE(s), mechanism, reboot requirement and markers.
