"""NCS domain brief injected into router + synthesize prompts.

Distilled from NCS 25.7 Product Description (DN1000039717), Guide to Documentation
(DN1000048602), and Troubleshooting Guide (DN1000065220). This is what grounds the
advisor's understanding of the system — keep it factual and in NCS's own vocabulary.
"""

NCS_BRIEF = """\
WHAT NCS IS
Nokia Container Services (NCS) is an on-premises, infrastructure-independent \
Container-as-a-Service (CaaS) platform built on Kubernetes, for running CNFs and \
containerized apps. On top of Kubernetes it adds Helm package management, Istio \
service mesh, backup, integrated logging/monitoring, and a REST API + `ncs` CLI.

NODE ROLES
- Controller/master nodes (×3, HA): Kubernetes control plane + container registry; \
not scale-out — a failed controller is recovered via a dedicated Replace operation.
- Worker nodes: run CNFs/apps; NCS's own services also run here.
- Edge nodes: external VIP + interface to the public network.
- Storage nodes: Ceph-based persistent storage (optional/dedicated).
- Deployer node: VIRTUALIZED only — a VM that performs the initial cluster deploy. \
On BareMetal, NCS Manager plays this role instead.
- Central management / monitoring nodes: Multi-NCS only.
Small setups collapse roles onto a minimum of 3 nodes ("AllinOne").

DEPLOYMENT FLAVORS (procedures diverge by flavor)
- BareMetal: physical nodes, deployed from the NCS Manager GUI; storage = Ceph (Rook).
- Virtualized (CBIS/OpenStack): VMs via a Deployer VM; OpenTofu creates infra (CLCM \
phase), then BCMT installs Kubernetes/NCS (BCMT phase). Storage = Rook/Ceph + GlusterFS.
- OpenStack SR-IOV: virtualized variant with SR-IOV/DPDK passthrough NICs.
Also standalone vs Multi-NCS/Central. OpenStack bare-metal provisioning uses Ironic.

LIFECYCLE (LCM)
Install/deploy via OneKey (`ncs` CLI, virtualized only — NOT BareMetal) or NCS Manager; \
runs staged Installation Steps (Bootstrap CM data → Generate Inventory → Bootstrap \
Manager → Provision Nodes → Post Config → Cluster Pre/Install/Post → Smoke Test). \
Day-2 LCM ops: Create, Heal, Scale (no auto-elasticity on BareMetal), Terminate, \
Upgrade, Reboot, Power mgmt, Replace master/manager. Separate OS upgrade + security patch.

KEY SUBSYSTEMS (real components)
Networking/CNI: Calico (default; WireGuard encryption is a trouble area), Multus, SR-IOV, \
Macvlan, VIP/SNAT/DualStack. Storage: Ceph (Rook, OSDs, PGs, pools) + GlusterFS; CSI/PV/PVC. \
ETCD: k8s backing store (slow-etcd hits scheduler/controller-manager). Deployer engine: \
BCMT (installer) + CLCM (OpenTofu resources); CM data = config state in Redis; NCM = central \
mgmt server. Registry/charts: Harbor. NCS Manager (BareMetal deploy/LCM GUI). NCS Portal \
(tenant/ops UI). Alarms/monitoring: VictoriaMetrics (metrics) + SearchStack (logs; alarm \
families CKEY/CALM/CVLK). Security/auth: LDAP (support differs by node role), RBAC, OPA, \
Keycloak (identity), certificate mgmt (CMPv2), Secure Boot; OpenSSH/SSH-port checks are a \
first-line deploy diagnostic. DNS: CoreDNS + NodeLocal cache. Backup: Avamar/CBUR.
Logs live under /opt/bcmt/log on the deployment/control nodes.

TRIAGE DIMENSIONS (what to pin down before concluding)
1. Lifecycle phase when it occurred (which install step / upgrade / heal / scale / day-2).
2. Deployment flavor (BareMetal vs virtualized/OpenStack vs SR-IOV; standalone vs Multi-NCS).
3. Node role affected (controller, worker, edge, storage, deployer, central) + node state \
(e.g. "Wait for a callback", "Deploy failed").
4. Subsystem (networking/Ceph/GlusterFS/ETCD/BCMT-CLCM/Harbor/Manager/Portal/SearchStack/\
Keycloak/DNS/API-CLI).
5. Exact error/alarm/state text (verbatim message, failing task name, alarm code, pod state \
like CrashLoopBackOff).
6. Scope (single node / cluster-wide / tenant) and which logs the engineer already has."""
