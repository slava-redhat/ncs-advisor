#!/usr/bin/env python3
"""Deploy the NCS Advisor podman-compose stack to a remote VM over SSH.

Same stack as local `make up` (db + ui containers reaching a host Ollama), just
running on a VM you point at by IP. No cloud APIs: it SSHes in non-interactively,
rsyncs this repo, and runs the compose there.

  check    VM reachable + podman/podman-compose/make/ollama present and serving
  setup    install prerequisites on the VM (podman, podman-compose, ollama, model)
  deploy   rsync the repo to the VM and bring the stack up (+ ingest)
  down     stop the stack on the VM (keeps the data volume)
  selfcheck  offline: assert ssh/rsync command construction (used by CI)

The IP/host is a required argument (--host). SSH is silent (BatchMode=yes): key
auth only, it fails fast instead of prompting for a password.
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PODMAN_DIR = Path(__file__).resolve().parent
REMOTE_DIR_DEFAULT = "ncs-advisor"
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# Silent, non-interactive SSH: no password prompts, auto-trust a new host key,
# give up quickly if the VM is down.
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new"]


@dataclass(frozen=True)
class Target:
    host: str
    user: str
    port: int
    identity: str | None
    remote_dir: str

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.host}"

    def ssh_base(self) -> list[str]:
        cmd = ["ssh", *SSH_OPTS, "-p", str(self.port)]
        if self.identity:
            cmd += ["-i", self.identity]
        cmd.append(self.destination)
        return cmd

    def rsync_ssh(self) -> str:
        parts = ["ssh", *SSH_OPTS, "-p", str(self.port)]
        if self.identity:
            parts += ["-i", self.identity]
        return " ".join(parts)


def run(args: list[str], *, capture: bool = False, check: bool = True,
        input_text: str | None = None) -> str:
    print("+", " ".join(shlex.quote(a) for a in args))
    completed = subprocess.run(
        args, text=True, input=input_text,
        stdout=subprocess.PIPE if capture else None, check=check)
    return (completed.stdout or "") if capture else ""


def ssh(target: Target, remote_cmd: str, *, capture: bool = False,
        check: bool = True, input_text: str | None = None) -> str:
    return run([*target.ssh_base(), remote_cmd], capture=capture, check=check,
               input_text=input_text)


def require_tools(*tools: str) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        raise SystemExit(f"Missing local command(s): {', '.join(missing)}")


def normalize_proxy(value: str | None) -> str | None:
    """Accept a bare URL or a 'http_proxy=URL' form (as the user may type it)."""
    if not value:
        return None
    if "=" in value and value.split("=", 1)[1].startswith(("http://", "https://")):
        return value.split("=", 1)[1]
    return value


def proxy_exports(proxy: str | None) -> str:
    """Shell prefix that exports the proxy for a remote non-login command (make/
    podman). NO_PROXY keeps in-cluster traffic (db, host Ollama) off the proxy."""
    if not proxy:
        return ""
    q = shlex.quote(proxy)
    no = "localhost,127.0.0.1,::1,db,host.containers.internal"
    return (f"export http_proxy={q} https_proxy={q} HTTP_PROXY={q} HTTPS_PROXY={q} "
            f"no_proxy={no} NO_PROXY={no}; ")


def ssh_ok(target: Target) -> bool:
    return subprocess.run([*target.ssh_base(), "true"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=False).returncode == 0


def remote_has(target: Target, cmd: str) -> bool:
    return subprocess.run(
        [*target.ssh_base(), f"command -v {shlex.quote(cmd)} >/dev/null 2>&1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0


def require_reachable(target: Target) -> None:
    require_tools("ssh")
    if not ssh_ok(target):
        raise SystemExit(
            f"SSH to {target.destination}:{target.port} failed (BatchMode = key auth only).\n"
            f"  - is the VM up and the IP correct?\n"
            f"  - install your key first:  ssh-copy-id -p {target.port} {target.destination}\n"
            f"  - or pass --identity /path/to/key")


def check(target: Target) -> None:
    print(f"== checking {target.destination}:{target.port} ==")
    require_reachable(target)
    print("ssh: ok")
    tools_ok = True
    for tool in ("podman", "podman-compose", "make", "rsync", "ollama"):
        ok = remote_has(target, tool)
        tools_ok = tools_ok and ok
        print(f"{tool}: {'ok' if ok else 'MISSING'}")
    api = ssh(target, "curl -fsS --max-time 5 http://127.0.0.1:11434/api/version",
              capture=True, check=False).strip()
    print(f"ollama api: {'ok ' + api if api else 'NOT serving on 127.0.0.1:11434'}")
    has_model = ssh(
        target, f"ollama list 2>/dev/null | grep -q {shlex.quote(EMBED_MODEL)} "
        "&& echo yes || echo no", capture=True, check=False).strip() == "yes"
    print(f"embed model {EMBED_MODEL}: {'present' if has_model else 'MISSING'}")
    if not (tools_ok and api and has_model):
        raise SystemExit("VM is missing prerequisites — run:  deploy.py setup --host "
                         f"{target.host}")
    print("\nall prerequisites present.")


def setup(target: Target, proxy: str | None, disable_selinux: bool = False,
          open_ports: str | None = None) -> None:
    print(f"== setting up prerequisites on {target.destination} =="
          + (f" (proxy {proxy})" if proxy else ""))
    require_reachable(target)
    script = (PODMAN_DIR / "setup.sh").read_text(encoding="utf-8")
    # Pipe the script into a remote bash — works before rsync/podman even exist.
    # Env vars tell setup.sh what optional steps to run (proxy/selinux/ports).
    envs = []
    if proxy:
        envs.append(f"NCS_PROXY={shlex.quote(proxy)}")
    if disable_selinux:
        envs.append("NCS_DISABLE_SELINUX=1")
    if open_ports:
        envs.append(f"NCS_OPEN_PORTS={shlex.quote(open_ports)}")
    prefix = (" ".join(envs) + " ") if envs else ""
    ssh(target, f"{prefix}bash -s", input_text=script)
    print("\nsetup done. Verify with:  deploy.py check --host " + target.host)


def deploy(target: Target, do_ingest: bool, proxy: str | None) -> None:
    print(f"== deploying to {target.destination}:{target.remote_dir} ==")
    require_tools("ssh", "rsync")
    require_reachable(target)
    if not (ROOT / ".env").exists():
        raise SystemExit("Create .env from .env.example before deploying "
                         "(it is rsynced to the VM so the stack can run).")
    excludes = [".git", "__pycache__", "*.pyc", "infra/gcp/backups", "infra/ocp/backups"]
    exclude_args = [arg for e in excludes for arg in ("--exclude", e)]
    # Trailing slash on source: copy the repo *contents* into remote_dir.
    run(["rsync", "-az", "--delete", "-e", target.rsync_ssh(), *exclude_args,
         f"{ROOT}/", f"{target.destination}:{target.remote_dir}/"])
    remote = shlex.quote(target.remote_dir)
    # The ADC file is mounted into the ui container, which runs under a userns
    # mapping (podman-compose.yml) — its rsynced 600 perms make it unreadable by
    # the mapped in-container user (EACCES). Make it world-readable if present.
    ssh(target, f"cd {remote} && [ -f .application_default_credentials.json ] && "
                "chmod 0644 .application_default_credentials.json || true")
    exports = proxy_exports(proxy)  # podman image pulls / builds need the proxy
    ssh(target, f"cd {remote} && {exports}make up")
    if do_ingest:
        ssh(target, f"cd {remote} && {exports}make ingest")
    print("\nNCS Advisor is up on the VM.")
    print(f"  direct (if the VM firewall allows 8501):  http://{target.host}:8501")
    print(f"  or tunnel:  ssh -p {target.port} -L 8501:localhost:8501 {target.destination}")
    print("              then browse http://localhost:8501")


def down(target: Target) -> None:
    require_reachable(target)
    ssh(target, f"cd {shlex.quote(target.remote_dir)} && make down")


def selfcheck() -> None:
    t = Target(host="1.2.3.4", user="cloud", port=2222, identity="/k", remote_dir="ncs-advisor")
    assert t.ssh_base() == [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new", "-p", "2222", "-i", "/k",
        "cloud@1.2.3.4"], t.ssh_base()
    assert t.rsync_ssh() == ("ssh -o BatchMode=yes -o ConnectTimeout=10 "
                             "-o StrictHostKeyChecking=accept-new -p 2222 -i /k"), t.rsync_ssh()
    # no identity -> no -i
    t2 = Target(host="h", user="u", port=22, identity=None, remote_dir="d")
    assert "-i" not in t2.ssh_base() and t2.ssh_base()[-1] == "u@h"
    # proxy: accept a bare URL or a "http_proxy=URL" form; empty -> None
    assert normalize_proxy("http://p:8080") == "http://p:8080"
    assert normalize_proxy("http_proxy=http://p:8080") == "http://p:8080"
    assert normalize_proxy(None) is None and normalize_proxy("") is None
    assert proxy_exports(None) == "" and "HTTP_PROXY=http://p:8080" in proxy_exports("http://p:8080")
    print("ok: podman deploy command-construction self-check passed")


def _target(args: argparse.Namespace) -> Target:
    return Target(host=args.host, user=args.user, port=args.port,
                  identity=args.identity, remote_dir=args.remote_dir)


def _add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", required=True, help="VM IP or hostname")
    parser.add_argument("--user", default=os.environ.get("NCS_SSH_USER") or os.environ.get("USER", "root"),
                        help="SSH user (default: $NCS_SSH_USER or $USER)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("NCS_SSH_PORT", "22")))
    parser.add_argument("--identity", default=os.environ.get("NCS_SSH_KEY"),
                        help="SSH private key path (default: $NCS_SSH_KEY or ssh-agent)")
    parser.add_argument("--remote-dir", default=REMOTE_DIR_DEFAULT,
                        help=f"target directory on the VM (default: {REMOTE_DIR_DEFAULT})")
    parser.add_argument("--proxy", default=os.environ.get("NCS_PROXY"),
                        help="HTTP(S) proxy URL for the VM's dnf/pip/ollama/podman "
                        "(e.g. http://host:8080). Accepts a bare URL or 'http_proxy=URL'.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    for name, help_text in (("check", "Verify the VM is reachable + has prerequisites"),
                            ("down", "Stop the stack on the VM (keeps data)")):
        _add_target_args(sub.add_parser(name, help=help_text))
    setup_parser = sub.add_parser("setup", help="Install prerequisites on the VM")
    _add_target_args(setup_parser)
    setup_parser.add_argument("--disable-selinux", action="store_true",
                              help="Set SELinux to permissive on the VM (avoids "
                              "rootless-podman container-label denials)")
    setup_parser.add_argument("--open-ports", nargs="?", const="8501", default=None,
                              help="Open TCP port(s) in firewalld, comma-separated "
                              "(default 8501 if the flag is given without a value)")
    deploy_parser = sub.add_parser("deploy", help="Rsync the repo and bring the stack up")
    _add_target_args(deploy_parser)
    deploy_parser.add_argument("--skip-ingest", action="store_true",
                               help="Deploy without running `make ingest` on the VM")
    sub.add_parser("selfcheck", help="Offline command-construction self-check (CI)")

    args = parser.parse_args()
    if args.action == "selfcheck":
        return selfcheck()
    target = _target(args)
    proxy = normalize_proxy(args.proxy)
    if args.action == "check":
        check(target)
    elif args.action == "setup":
        setup(target, proxy, args.disable_selinux, args.open_ports)
    elif args.action == "deploy":
        deploy(target, not args.skip_ingest, proxy)
    else:
        down(target)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
