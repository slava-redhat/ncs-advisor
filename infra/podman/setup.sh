#!/usr/bin/env bash
# Initial prerequisites for the NCS Advisor podman-compose stack on a fresh VM.
# Idempotent — safe to re-run. Invoked over SSH by:  deploy.py setup --host <ip>
# Installs: podman, podman-compose, make, rsync, curl, ollama; then configures
# ollama to listen on 0.0.0.0 (so the containers reach it via
# host.containers.internal) and pulls the embedding model.
#
# Behind a proxy: pass NCS_PROXY (deploy.py --proxy). It is applied to dnf, pip,
# the ollama systemd service, and the system environment so every network step
# below works on a proxied VM.
set -euo pipefail

EMBED_MODEL="${OLLAMA_EMBED_MODEL:-nomic-embed-text}"
PROXY="${NCS_PROXY:-}"
# Include the in-cluster names: podman injects this NO_PROXY into the containers,
# and the host Ollama (host.containers.internal) + db must NOT be proxied, or the
# embed call 504s through the corp proxy. Matches deploy.py's proxy_exports list.
NO_PROXY_LIST="localhost,127.0.0.1,::1,db,host.containers.internal"
log() { printf '\n== %s ==\n' "$*"; }
sudo_() { if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi; }

if command -v dnf >/dev/null 2>&1; then PM=dnf
elif command -v apt-get >/dev/null 2>&1; then PM=apt
else echo "unsupported distro (need dnf or apt-get)"; exit 1; fi

# --- proxy: export for this script + persist for dnf and the system env --------
if [ -n "$PROXY" ]; then
  log "configuring proxy: $PROXY"
  export http_proxy="$PROXY" https_proxy="$PROXY" HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY"
  export no_proxy="$NO_PROXY_LIST" NO_PROXY="$NO_PROXY_LIST"
  # dnf reads its own proxy= from dnf.conf (systemd-run dnf ignores shell env)
  if [ "$PM" = dnf ] && ! grep -q "^proxy=$PROXY$" /etc/dnf/dnf.conf 2>/dev/null; then
    sudo_ sed -i '/^proxy=/d' /etc/dnf/dnf.conf 2>/dev/null || true
    echo "proxy=$PROXY" | sudo_ tee -a /etc/dnf/dnf.conf >/dev/null
  fi
  # system-wide env for future shells / podman
  printf 'http_proxy=%s\nhttps_proxy=%s\nHTTP_PROXY=%s\nHTTPS_PROXY=%s\nno_proxy=%s\nNO_PROXY=%s\n' \
    "$PROXY" "$PROXY" "$PROXY" "$PROXY" "$NO_PROXY_LIST" "$NO_PROXY_LIST" \
    | sudo_ tee /etc/profile.d/ncs-proxy.sh >/dev/null
fi

# --- optionally set SELinux permissive (NCS_DISABLE_SELINUX=1) -----------------
# Enforcing SELinux is the source of the container file-label denials we hit with
# rootless podman (the :Z/:z ADC mount). Permissive keeps auditing but stops the
# denials, effective immediately without a reboot.
if [ "${NCS_DISABLE_SELINUX:-}" = "1" ] && command -v getenforce >/dev/null 2>&1; then
  log "setting SELinux to permissive"
  sudo_ setenforce 0 2>/dev/null || true
  [ -f /etc/selinux/config ] && sudo_ sed -i 's/^SELINUX=.*/SELINUX=permissive/' /etc/selinux/config
fi

install_pkg() {
  if [ "$PM" = dnf ]; then sudo_ dnf install -y "$@"
  else sudo_ apt-get update -y && sudo_ apt-get install -y "$@"; fi
}

# pip install that works across pip versions (old pip has no --break-system-packages)
# and honours the proxy.
pip_install() {
  local px=(); [ -n "$PROXY" ] && px=(--proxy "$PROXY")
  sudo_ python3 -m pip install "${px[@]}" "$@" --break-system-packages 2>/dev/null \
    || sudo_ python3 -m pip install "${px[@]}" "$@" 2>/dev/null \
    || python3 -m pip install --user "${px[@]}" "$@"
}

for tool in curl make rsync; do
  command -v "$tool" >/dev/null 2>&1 || { log "installing $tool"; install_pkg "$tool"; }
done

command -v podman >/dev/null 2>&1 || { log "installing podman"; install_pkg podman; }

# Build RUN steps via chroot isolation. On restricted VMs (nested virt / limited
# cgroups) the OCI runtime can't create the build container and buildah dies with
# "did not get container create message from subprocess: EOF" before pip runs.
# Persist it system-wide so a bare `podman-compose up` works too (make up sets it
# itself). Override with BUILDAH_ISOLATION=oci if the runtime is actually fine.
echo 'export BUILDAH_ISOLATION=chroot' | sudo_ tee /etc/profile.d/ncs-buildah.sh >/dev/null

# Same class of restriction at RUN time: the systemd cgroup manager can't install
# the eBPF device filter on cgroup v2 here ("crun: systemd failed to install eBPF
# device filter"), so containers won't start. cgroupfs lets crun install it
# directly. Non-destructive drop-in; existing pods must be recreated (down/up).
sudo_ mkdir -p /etc/containers/containers.conf.d
printf '%s\n' '[engine]' 'cgroup_manager="cgroupfs"' \
  | sudo_ tee /etc/containers/containers.conf.d/90-cgroupfs.conf >/dev/null

if ! command -v podman-compose >/dev/null 2>&1; then
  log "installing podman-compose"
  # Not packaged everywhere (e.g. CentOS Stream 10 has no podman-compose rpm), so
  # fall back to pip (proxy-aware, old-pip-safe).
  if ! install_pkg podman-compose 2>/dev/null; then
    command -v python3 >/dev/null 2>&1 || install_pkg python3
    python3 -m pip --version >/dev/null 2>&1 || install_pkg python3-pip
    pip_install podman-compose
  fi
fi
command -v podman-compose >/dev/null 2>&1 \
  || { echo "ERROR: podman-compose still not installed — check proxy/network and re-run"; exit 1; }

# Install (or REPAIR) ollama. A proxy-less/interrupted install leaves 0-byte
# runner binaries (llama-server, libggml-*), so serve/pull work but embedding
# fails with "exec format error" — reinstall if the runner isn't a real file.
if ! command -v ollama >/dev/null 2>&1 || [ ! -s /usr/local/lib/ollama/llama-server ]; then
  log "installing/repairing ollama"
  curl -fsSL https://ollama.com/install.sh | sh
fi

# ollama must listen on all interfaces so the containers reach it via
# host.containers.internal (host-gateway), and needs the proxy (in its service
# env, not the shell) to pull models. Prefer systemd; fall back to a background
# serve if systemd doesn't manage it.
if command -v systemctl >/dev/null 2>&1 \
   && systemctl list-unit-files 2>/dev/null | grep -q '^ollama.service'; then
  log "configuring ollama systemd service (0.0.0.0:11434${PROXY:+, proxy})"
  sudo_ systemctl unmask ollama 2>/dev/null || true
  sudo_ mkdir -p /etc/systemd/system/ollama.service.d
  {
    echo "[Service]"
    echo 'Environment="OLLAMA_HOST=0.0.0.0:11434"'
    if [ -n "$PROXY" ]; then
      echo "Environment=\"HTTP_PROXY=$PROXY\""
      echo "Environment=\"HTTPS_PROXY=$PROXY\""
      echo "Environment=\"NO_PROXY=$NO_PROXY_LIST\""
    fi
  } | sudo_ tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null
  sudo_ systemctl daemon-reload
  sudo_ systemctl enable --now ollama
  sudo_ systemctl restart ollama
else
  log "starting ollama serve in the background on 0.0.0.0:11434"
  pgrep -x ollama >/dev/null 2>&1 \
    || (OLLAMA_HOST=0.0.0.0:11434 http_proxy="$PROXY" https_proxy="$PROXY" \
        nohup ollama serve >/tmp/ollama.log 2>&1 &)
fi

log "waiting for the ollama API"
for _ in $(seq 1 30); do
  curl -fsS --max-time 3 --noproxy '*' http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break
  sleep 2
done

log "pulling embed model: $EMBED_MODEL"
ollama pull "$EMBED_MODEL"

# --- optionally open firewall ports (NCS_OPEN_PORTS="8501,...") -----------------
if [ -n "${NCS_OPEN_PORTS:-}" ]; then
  if command -v firewall-cmd >/dev/null 2>&1 && sudo_ systemctl is-active --quiet firewalld; then
    log "opening firewall ports: $NCS_OPEN_PORTS"
    IFS=',' read -ra _ports <<< "$NCS_OPEN_PORTS"
    for p in "${_ports[@]}"; do sudo_ firewall-cmd --permanent --add-port="${p}/tcp"; done
    sudo_ firewall-cmd --reload
  else
    log "firewalld not active — ports $NCS_OPEN_PORTS need no action"
  fi
fi

# Keep rootless services alive after logout (so the stack survives the SSH session).
command -v loginctl >/dev/null 2>&1 && sudo_ loginctl enable-linger "$(id -un)" 2>/dev/null || true

log "setup complete"
