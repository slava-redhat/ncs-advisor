# NCS Advisor GKE Workflow

This deployment prepares the GKE equivalents of the persistent pgvector
database, the private in-cluster Ollama embedding service, and the Streamlit
`ui`. There are only **three deployables** — `postgres`, `ollama`, and `ui` —
because the LangGraph runs **in-process inside the `ui`** (there is no separate
orchestrator/API service). The vector corpus is deliberately cloned from the
local database rather than re-ingested in GKE. All provisioning and deploy logic
is in `infra/gcp/gke.py`; no shell wrapper is required.

## Prerequisites

- `gcloud`, `kubectl`, and the GKE auth plugin.
- A GCP project in which you can create GKE, Artifact Registry, and Cloud Build
  resources.
- Vertex access for the selected `NCS_LLM_MODEL` in `NCS_VERTEX_LOCATION`.

### Isolated GCP authentication environment

Create and use a dedicated Conda environment so GCP authentication and deployment
commands do not modify the current Python environment. The Python deployment
tools use only the standard library, so **do not install any Python packages** in
it.

```bash
conda create -n gcp-auth python=3.12 -y
conda activate gcp-auth
python --version
```

Install `gcloud`, `kubectl`, and the GKE auth plugin through the operating-system
package manager or Google Cloud SDK; they are command-line tools, not Conda
packages.

Conda does not isolate `gcloud` credentials by itself. Configure this environment
to use its own Google Cloud SDK directory, then reactivate it:

```bash
conda env config vars set CLOUDSDK_CONFIG="$HOME/.config/gcloud-gcp-auth"
conda deactivate
conda activate gcp-auth
echo "$CLOUDSDK_CONFIG"
```

With `gcp-auth` active, install the GKE kubectl auth plugin if it is not already
available, then authenticate:

```bash
gcloud components install kubectl gke-gcloud-auth-plugin
gcloud auth login
```

The active Conda environment and its `CLOUDSDK_CONFIG` setting last only for the
current shell. Exit it after GCP work with `conda deactivate`.

## Configuration

The Python command reads configuration from environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `GCP_PROJECT_ID` | current gcloud project | GCP project |
| `GCP_REGION` | `us-central1` | Artifact Registry region |
| `GCP_ZONE` | `${GCP_REGION}-a` | Single-node GKE zone |
| `GKE_CLUSTER_NAME` | `ncs` | GKE cluster name |
| `ARTIFACT_REPOSITORY` | `ncs` | Docker repository name |
| `K8S_NAMESPACE` | `ncs` | Kubernetes namespace |
| `GKE_MACHINE_TYPE` | `e2-standard-2` | Resource tier |
| `GSA_EMAIL` | `ncs-vertex@<PROJECT_ID>.iam.gserviceaccount.com` | Google SA bound to the `ncs` KSA for Vertex |

Create `.env` from `.env.example` and set a non-default `POSTGRES_PASSWORD`. The
non-secret runtime values (Vertex project/location/model, Ollama model/host, PG
host/port) live in `infra/gcp/k8s/ui/configmap.yaml` (`ncs-config`) — edit
`ANTHROPIC_VERTEX_PROJECT_ID` there for your project. Only the Postgres
credentials are pushed into the `ncs-secrets` Secret from `.env`.

```bash
cp .env.example .env
export GCP_PROJECT_ID="your-project"
```

## How the ui reaches Vertex: Workload Identity

On GKE the `ui` obtains Claude-on-Vertex credentials through **Workload
Identity** — the `ncs` Kubernetes ServiceAccount is annotated with a Google SA,
and the pod pulls short-lived tokens from the GKE metadata server. **No ADC key
file is mounted in the cluster.** (On OpenShift, which has no Workload Identity,
the `ui` instead mounts an `application_default_credentials.json` Secret — see
`infra/ocp/README.md`.)

`provision` creates the cluster with `--workload-pool`. Create the Google SA and
the KSA↔GSA IAM binding once (replace `PROJECT_ID`):

```bash
gcloud iam service-accounts create ncs-vertex \
  --display-name="NCS Advisor Vertex" --project "PROJECT_ID"

# Let ncs-vertex call Vertex AI (Claude-on-Vertex).
gcloud projects add-iam-policy-binding "PROJECT_ID" \
  --member="serviceAccount:ncs-vertex@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# Bind the ncs KSA to the GSA (namespace defaults to ncs).
gcloud iam service-accounts add-iam-policy-binding \
  "ncs-vertex@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:PROJECT_ID.svc.id.goog[ncs/ncs]" \
  --project "PROJECT_ID"
```

The `serviceaccount.yaml` KSA annotation (`iam.gke.io/gcp-service-account`) is
rendered from `GSA_EMAIL`; override it if you named the GSA differently.

## 1. Provision

```bash
python3 infra/gcp/gke.py provision
```

This enables the required APIs, creates the Artifact Registry repository, creates
a single-node GKE cluster **with Workload Identity enabled**, and installs
ingress-nginx.

## 2. Deploy

```bash
python3 infra/gcp/gke.py deploy
python3 infra/gcp/gke.py deploy --tag v1.0.0
```

The command builds and pushes the `ui` image with Cloud Build, creates the
`ncs-secrets` Secret from `.env`, applies the `ncs-config` ConfigMap and the
rendered manifests. It also deploys a private, persistent Ollama service and
pulls `nomic-embed-text`; the `ui` reaches it at `http://ollama:11434`. To copy a
later `.env` change into the cluster:

```bash
python3 infra/gcp/gke.py sync-secrets
```

## 3. Clone the local vector database

After changing docs/solutions, update the local corpus first. `make ingest` is
incremental; `make reingest` clears and re-embeds the whole corpus. Keep the
local `db` container running and create a compressed backup:

```bash
make up
make ingest
make stats
make vector-db-backup
```

This creates `infra/gcp/backups/ncs-vector.sql.gz` (all `doc_chunk` rows and
their pgvector embeddings plus the ingest ledger).

Provision and deploy first. Then configure `kubectl` for the cluster, wait until
the Postgres pod is ready, and restore:

```bash
gcloud container clusters get-credentials "${GKE_CLUSTER_NAME:-ncs}" \
  --zone="${GCP_ZONE:-us-central1-a}" \
  --project="${GCP_PROJECT_ID}"
kubectl -n "${K8S_NAMESPACE:-ncs}" rollout status statefulset/postgres --timeout=180s
make vector-db-restore
```

The restore scales the `ui` to zero, replaces the data in Postgres with the
backup, then restores the original replica count. Use the same custom path for
both commands when retaining snapshots:

```bash
make vector-db-backup VECTOR_DB_BACKUP=/secure/backups/ncs-2026-08-22.sql.gz
make vector-db-restore VECTOR_DB_BACKUP=/secure/backups/ncs-2026-08-22.sql.gz
```

## 4. Operate

```bash
kubectl -n ncs get pods,svc,ingress
kubectl -n ncs logs deployment/ui --tail=100
```

### Access the ingress

The ingress controller creates an external load balancer whose address can take
several minutes. Wait for it and print the URL:

```bash
python3 infra/gcp/gke.py ingress --wait
```

Open the printed URL in a browser — it serves the Streamlit `ui` at `/`.

## 5. Upgrade Tier

GKE machine types cannot be changed in place. Create a replacement node pool,
drain the old node, then redeploy with the matching resource tier:

```bash
gcloud container node-pools create ncs-upgrade \
  --cluster=ncs --zone="${GCP_ZONE:-us-central1-a}" \
  --machine-type=e2-standard-4 --num-nodes=1 --disk-size=30
kubectl get nodes
kubectl cordon <old-node>
kubectl drain <old-node> --ignore-daemonsets --delete-emptydir-data
gcloud container node-pools delete default-pool \
  --cluster=ncs --zone="${GCP_ZONE:-us-central1-a}"
GKE_MACHINE_TYPE=e2-standard-4 python3 infra/gcp/gke.py deploy
```

## 6. Teardown

This irreversibly removes the cluster and Artifact Registry images:

```bash
python3 infra/gcp/gke.py teardown --yes
```

## Resource Tiers

The tier controls the single GKE node and the resource requests/limits rendered
into the Postgres, Ollama, and `ui` manifests. Ollama is CPU-only and keeps
`nomic-embed-text` on a 5Gi persistent volume.

| Tier | Node | Node estimate/mo* | Use case |
|------|------|---:|---|
| `e2-medium` | 1 shared vCPU, 4GB | ~$25 | Manifest smoke test only; expect slow, contended embedding |
| `e2-standard-2` | 2 vCPU, 8GB | ~$49 | Low-volume proof of concept; one interactive request at a time |
| `e2-standard-4` | 4 vCPU, 16GB | ~$97 | **Recommended tier**; responsive Ollama retrieval + headroom |
| `e2-standard-8` | 8 vCPU, 32GB | ~$194 | Concurrent interactive users or sustained retrieval load |

\*Node estimates are planning figures only. They exclude persistent disks, the
ingress load balancer, Artifact Registry storage, Cloud Build, network egress,
taxes, and Vertex AI model calls. Use the [Google Cloud Pricing Calculator](https://cloud.google.com/products/calculator)
for the selected region and current price.

### Selecting a tier

Use `e2-standard-4` for a normal interactive RAG deployment:

```bash
export GKE_MACHINE_TYPE=e2-standard-4
python3 infra/gcp/gke.py provision
python3 infra/gcp/gke.py deploy
```

Vertex model calls, not vector retrieval, dominate end-to-end advice latency. Do
not use Spot nodes for this single-node deployment: Postgres and Ollama are
stateful, so eviction makes the service unavailable until recovery.
