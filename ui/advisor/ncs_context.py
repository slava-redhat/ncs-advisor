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
On CN-B (BareMetal), NCS Manager plays this role instead.
- Central management / monitoring nodes: Multi-NCS only.
Small setups collapse roles onto a minimum of 3 nodes ("AllinOne").

DEPLOYMENT FLAVORS (official shorthand from support tickets/CLI Guide: CN-A / CN-B — \
procedures diverge by flavor, DO NOT MIX these two)
- CN-B (BareMetal): physical nodes, deployed/managed via the NCS Manager GUI (no \
Deployer VM). Nodes are provisioned directly over BMC/IPMI/Redfish (boot order, virtual \
media) as documented in the BareMetal Installation Guide — this BMC/IPMI/BIOS-boot-order \
troubleshooting IS legitimate for CN-B. What is NEVER CN-B: the `openstack` CLI, Ironic, \
CBIS, a Deployer VM, OpenTofu/CLCM, or Ironic's state model (e.g. "wait-call-back", \
"deploy failed" node states) — those belong only to CN-A, even though CN-A's underlying \
compute hosts are also physical/"bare-metal" servers. Known hardware profiles in support \
tickets: CN-B config4 (Master node runs bcmt-admin) / CN-B config5 (Central node runs \
bcmt-admin). Storage = Ceph (Rook).
- CN-A (Virtualized — runs on Nokia CBIS or vanilla/generic OpenStack, both are CN-A): \
VMs via a Deployer VM ("Deploy server"); OpenTofu creates infra (CLCM phase), then BCMT \
installs Kubernetes/NCS (BCMT phase). Storage = Rook/Ceph + GlusterFS. OpenStack itself \
provisions its own underlying physical compute hosts via Ironic ("OpenStack bare-metal \
provisioning", `openstack baremetal node list/show`, wait-call-back/deploy-failed states) \
— this Ironic/`openstack \
baremetal ...` tooling is part of CN-A's infrastructure layer, never part of CN-B.
- CN-A SR-IOV: CN-A variant with SR-IOV/DPDK passthrough NICs.
Also standalone vs Multi-NCS/Central.

LIFECYCLE (LCM)
Install/deploy via OneKey (`ncs` CLI, CN-A only — NOT CN-B) or NCS Manager; \
runs staged Installation Steps (Bootstrap CM data → Generate Inventory → Bootstrap \
Manager → Provision Nodes → Post Config → Cluster Pre/Install/Post → Smoke Test). \
Day-2 LCM ops: Create, Heal, Scale (no auto-elasticity on CN-B), Terminate, \
Upgrade, Reboot, Power mgmt, Replace master/manager. Separate OS upgrade + security patch.

KEY SUBSYSTEMS (real components)
Networking/CNI: Calico (default; WireGuard encryption is a trouble area), Multus, SR-IOV, \
Macvlan, VIP/SNAT/DualStack. Storage: Ceph (Rook, OSDs, PGs, pools) + GlusterFS; CSI/PV/PVC. \
ETCD: k8s backing store (slow-etcd hits scheduler/controller-manager). Deployer engine: \
BCMT (installer) + CLCM (OpenTofu resources); CM data = config state in Redis; NCM = central \
mgmt server. Registry/charts: Harbor. NCS Manager (CN-B deploy/LCM GUI). NCS Portal \
(tenant/ops UI). Alarms/monitoring: VictoriaMetrics (metrics) + SearchStack (logs; alarm \
families CKEY/CALM/CVLK). Security/auth: LDAP (support differs by node role), RBAC, OPA, \
Keycloak (identity), certificate mgmt (CMPv2), Secure Boot; OpenSSH/SSH-port checks are a \
first-line deploy diagnostic. DNS: CoreDNS + NodeLocal cache. Backup: Avamar/CBUR.
Logs live under /opt/bcmt/log on the deployment/control nodes.

TRIAGE DIMENSIONS (what to pin down before concluding)
1. Lifecycle phase when it occurred (which install step / upgrade / heal / scale / day-2).
2. Deployment flavor (CN-B/BareMetal vs CN-A/virtualized-OpenStack vs CN-A-SR-IOV; standalone vs Multi-NCS).
3. Node role affected (controller, worker, edge, storage, deployer, central) + node state \
(e.g. "Wait for a callback", "Deploy failed").
4. Subsystem (networking/Ceph/GlusterFS/ETCD/BCMT-CLCM/Harbor/Manager/Portal/SearchStack/\
Keycloak/DNS/API-CLI).
5. Exact error/alarm/state text (verbatim message, failing task name, alarm code, pod state \
like CrashLoopBackOff).
6. Scope (single node / cluster-wide / tenant) and which logs the engineer already has."""
