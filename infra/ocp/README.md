# NCS Advisor on OpenShift (trial / Developer Sandbox)

Deploys the same stack as `infra/gcp/` — **three deployables**: postgres
(pgvector), ollama (in-cluster embeddings), and the Streamlit `ui` — to an
existing OpenShift project. The LangGraph runs **in-process inside the `ui`**;
there is no separate orchestrator/API service.

Differences from the GKE deployment (`infra/gcp/gke.py`):

| GKE                                    | OpenShift                                    |
|----------------------------------------|-----------------------------------------------|
| `gcloud container clusters create`     | not needed — trial project is pre-provisioned |
| Cloud Build + Artifact Registry        | `BuildConfig` (binary Docker build) + `ImageStream`, pushed to the project's internal registry |
| `Ingress` (nginx)                      | `Route` |
| Vertex creds via **Workload Identity** | Vertex ADC **mounted from a Secret** (`ncs-google-credentials`) — OpenShift has no Workload Identity |
| fixed `runAsUser: 1000` / `fsGroup: 1000` | no fixed UID — the default `restricted` SCC assigns one automatically |
| —                                      | ollama's `$HOME` (`/root`) relocated via `HOME=/data` — its default home isn't reachable under an arbitrary UID |

## 0. Prerequisites

- `oc` CLI installed and logged in: on the console, top-right → **Copy login
  command** → paste the `oc login --token=... --server=...` command.
- `.env` in the repo root (copy from `.env.example`) with a non-default
  `POSTGRES_PASSWORD`.
- The Vertex ADC key file at the repo root as `.application_default_credentials.json`
  (copy from `~/.config/gcloud/application_default_credentials.json`). It is
  pushed into the `ncs-google-credentials` Secret and mounted into the `ui` at
  `GOOGLE_APPLICATION_CREDENTIALS`. The mount is `optional`, so a pod without it
  still starts (Vertex calls fail until it is provided).
- Non-secret runtime env (Vertex project/location/model, Ollama, PG host/port)
  lives in `infra/ocp/k8s/ui/configmap.yaml` (`ncs-config`); edit
  `ANTHROPIC_VERTEX_PROJECT_ID` for your project.
- Select your project: `oc project <your-project>` (or set `OCP_NAMESPACE`).

## 1. Bootstrap (one-time)

```
python3 infra/ocp/openshift.py bootstrap
```

Creates the `ncs` ServiceAccount and the `ui` ImageStream + BuildConfig. No SCC
grant is needed by default — postgres (official image, arbitrary-UID via
nss_wrapper) and ollama (`$HOME` pointed at the PVC mount instead of `/root`)
both run under the default `restricted` SCC.

If a pod still fails with a permission error on your specific cluster, grant
`anyuid` as a troubleshooting escape hatch (requires project-admin rights):
```
python3 infra/ocp/openshift.py grant-anyuid
```

## 2. Deploy

```
python3 infra/ocp/openshift.py deploy
```

This:
1. Builds the `ui` image via `oc start-build ui --from-dir=ui --follow` (binary build — no git push/webhook needed).
2. Syncs secrets/configmaps (`ncs-secrets` from `.env`, `ncs-postgres-init` from `db/schema.sql`, optional `ncs-google-credentials` from `.application_default_credentials.json`).
3. Applies the `ncs-config` ConfigMap, postgres/ollama StatefulSets, and waits for rollout.
4. Applies the `ui` Deployment (image-triggers pick up the freshly built image automatically) and the Route.
5. Prints the Route URL.

## Restore an existing DB dump

The dump format is plain `pg_dump` gzip'd SQL — the same file works whether it
was created for GCP or here. Reuse `infra/gcp/backups/ncs-vector.sql.gz` (or
create a fresh one from your local podman-compose stack), via the repo-root
`make` targets (`TARGET=ocp` selects this script and `infra/ocp/backups/`):
```
make vector-db-restore TARGET=ocp VECTOR_DB_BACKUP=infra/gcp/backups/ncs-vector.sql.gz
# or, to make a new backup from your local stack first:
make vector-db-backup TARGET=ocp
make vector-db-restore TARGET=ocp
```
This scales the `ui` to 0, streams the dump into the `postgres-0` pod via
`oc exec`, then restores the original replica count.

## Other commands

```
python3 infra/ocp/openshift.py sync-secrets   # re-sync .env / schema.sql without rebuilding
python3 infra/ocp/openshift.py routes         # print route URL
python3 infra/ocp/openshift.py teardown --yes # delete all NCS Advisor resources in the project
```

## Storage quota

Trial/sandbox projects usually carry small storage quotas. Defaults here are
conservative (`POSTGRES_STORAGE=5Gi`, `OLLAMA_STORAGE=2Gi`); override via
environment variables if your project's quota allows more:
```
POSTGRES_STORAGE=20Gi OLLAMA_STORAGE=5Gi python3 infra/ocp/openshift.py deploy
```
