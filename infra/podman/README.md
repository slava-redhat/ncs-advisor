# Deploy to a VM with podman-compose

Runs the exact same stack as local `make up` — `db` + `ui` containers reaching a
host **Ollama** for embeddings — on a remote VM you address by IP, over SSH. No
cloud APIs; the driver SSHes in, installs prerequisites, rsyncs this repo, and
runs the compose there. Parallel to `infra/gcp` (GKE) and `infra/ocp` (OpenShift).

## Prerequisites (on your machine)

- `ssh` + `rsync` installed.
- **Key-based SSH to the VM already working** — the driver uses `BatchMode=yes`
  (no password prompts). Set it up once:
  ```bash
  ssh-copy-id <user>@<vm-ip>
  ```
- A local `.env` (from `.env.example`) and, for Vertex, the ADC file
  `.application_default_credentials.json`. Both are rsynced to the VM so the
  stack can run.

## Usage

```bash
# 1. is the VM up, reachable, and does it have what it needs?
python3 infra/podman/deploy.py check  --host 203.0.113.10

# 2. install prerequisites on a fresh VM (podman, podman-compose, ollama, model)
python3 infra/podman/deploy.py setup  --host 203.0.113.10

# 3. copy the repo up and bring the stack online (+ ingest all versions on disk)
python3 infra/podman/deploy.py deploy --host 203.0.113.10

# stop it (keeps the pgvector data volume)
python3 infra/podman/deploy.py down   --host 203.0.113.10
```

### Copy the embedded DB instead of re-ingesting

Like `infra/gcp` and `infra/ocp`, you can copy the already-embedded pgvector DB
to the VM instead of running `make ingest` there (skips re-embedding). Backup is
local; restore streams the dump over SSH into the VM's `db` container:

```bash
# back up the local DB (writes infra/podman/backups/ncs-vector.sql.gz)
make TARGET=podman vector-db-backup

# deploy the app without ingest, then restore the DB into the VM
python3 infra/podman/deploy.py deploy --host 203.0.113.10 --skip-ingest
make vector-db-restore TARGET=podman \
     SSH_HOST=203.0.113.10 SSH_USER=cloud-user SSH_KEY=$HOME/env-keys/key_all_666

# equivalently, direct:
python3 infra/podman/db_transfer.py backup  --output infra/podman/backups/ncs-vector.sql.gz
python3 infra/podman/db_transfer.py restore --input  infra/podman/backups/ncs-vector.sql.gz \
        --host 203.0.113.10
```

The dump format is identical across targets — a backup made under `gcp`/`ocp`
restores here and vice versa.

Common options (env-var fallbacks in parentheses):

| Flag | Meaning | Default |
|------|---------|---------|
| `--host` | VM IP or hostname (required) | — |
| `--user` | SSH user | `$NCS_SSH_USER` or `$USER` |
| `--port` | SSH port | `$NCS_SSH_PORT` or `22` |
| `--identity` | SSH private key path | `$NCS_SSH_KEY` or ssh-agent |
| `--remote-dir` | target dir on the VM | `ncs-advisor` |
| `--proxy` | HTTP(S) proxy URL for the VM | `$NCS_PROXY` |
| `--skip-ingest` | `deploy` without running ingest | off |

### Behind a proxy

If the VM reaches the internet only through a corporate proxy, pass `--proxy`.
It's applied to **dnf, pip, the ollama systemd service, and the system env** so
every network step works, and to podman image pulls during `deploy`:

```bash
python3 infra/podman/deploy.py setup  --host 10.38.65.27 \
  --user cloud-user --identity ~/env-keys/key_all_666 \
  --proxy http://proxy.example.net:8080
python3 infra/podman/deploy.py deploy --host 10.38.65.27 \
  --user cloud-user --identity ~/env-keys/key_all_666 \
  --proxy http://proxy.example.net:8080
```

A bare URL or a `http_proxy=http://…` form is accepted. In-cluster traffic
(the `db` container, the host Ollama) bypasses the proxy automatically.

## Reaching the UI

The compose publishes `8501`. Either open the VM firewall to it
(`http://<vm-ip>:8501`) or tunnel:

```bash
ssh -L 8501:localhost:8501 <user>@<vm-ip>   # then http://localhost:8501
```

## Notes

- **Ollama on the VM** must listen on `0.0.0.0:11434` so the rootless containers
  reach it via `host.containers.internal`; `setup` configures a systemd drop-in
  (or backgrounds `ollama serve`) to do exactly that. `check` verifies the API is
  serving and the embed model is pulled.
- **Idempotent.** `setup` is safe to re-run; `deploy` uses `rsync --delete`, so
  re-deploying just syncs the diff and restarts the stack.
- **`make ingest` runs on the VM** against the rsynced `data/NCS-*` folders — the
  same content as your local repo.
- Offline check used by CI: `python3 infra/podman/deploy.py selfcheck`.
