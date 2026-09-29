#!/usr/bin/env python3
"""Copy the local NCS Advisor pgvector database to/from Postgres running under
podman-compose on a remote VM. Mirrors gcp/db_transfer.py and ocp/db_transfer.py
— the dump format is identical, so a backup made here or under gcp/ocp restores
on any target. `backup` runs locally; `restore` streams the dump over SSH into
the VM's `db` container, so you copy the already-embedded DB instead of
re-running `make ingest` on the VM.

SSH options/credentials come from the same env vars as deploy.py
(NCS_SSH_HOST/USER/PORT/KEY) or the matching flags."""

from __future__ import annotations

import argparse
import gzip
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import deploy  # sibling module: Target + silent-SSH plumbing

ROOT = Path(__file__).resolve().parents[2]


def require(*commands: str) -> None:
    missing = [name for name in commands if shutil.which(name) is None]
    if missing:
        raise SystemExit(f"Missing required command(s): {', '.join(missing)}")


def backup(output: Path) -> None:
    require("podman-compose")
    output.parent.mkdir(parents=True, exist_ok=True)
    dump = subprocess.Popen(
        [
            "podman-compose", "--env-file", ".env", "exec", "-T", "db", "pg_dump",
            "--clean", "--if-exists", "-U", os.environ.get("POSTGRES_USER", "ncs"),
            "-d", os.environ.get("POSTGRES_DB", "ncs"),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
    )
    assert dump.stdout is not None
    with gzip.open(output, "wb") as archive:
        shutil.copyfileobj(dump.stdout, archive)
    if dump.wait() != 0:
        output.unlink(missing_ok=True)
        raise SystemExit("Local database backup failed.")
    print(f"Created {output}")


def restore(source: Path, target: deploy.Target) -> None:
    if not source.is_file():
        raise SystemExit(f"Backup file does not exist: {source}")
    require("ssh")
    deploy.require_reachable(target)
    user = os.environ.get("POSTGRES_USER", "ncs")
    db = os.environ.get("POSTGRES_DB", "ncs")
    dc = "podman-compose --env-file .env"
    # Stop ui, gunzip the streamed dump into the db container, then restart ui.
    # `exit $rc` propagates the psql result even though `up -d ui` runs after.
    remote_cmd = (
        f"cd {shlex.quote(target.remote_dir)} && {dc} stop ui && "
        f"gunzip -c | {dc} exec -T db psql -v ON_ERROR_STOP=1 -h 127.0.0.1 "
        f"-U {shlex.quote(user)} -d {shlex.quote(db)} ; rc=$? ; {dc} up -d ui ; exit $rc"
    )
    print("+", " ".join(shlex.quote(a) for a in [*target.ssh_base(), remote_cmd]))
    proc = subprocess.Popen([*target.ssh_base(), remote_cmd], stdin=subprocess.PIPE)
    assert proc.stdin is not None
    with open(source, "rb") as archive:  # stream the .gz as-is; remote gunzips
        shutil.copyfileobj(archive, proc.stdin)
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("Postgres restore failed.")
    print(f"Restored {source} into {target.destination}:{target.remote_dir} (db container)")


def _target(args: argparse.Namespace) -> deploy.Target:
    if not args.host:
        raise SystemExit("restore needs a VM: pass --host or set NCS_SSH_HOST.")
    return deploy.Target(host=args.host, user=args.user, port=args.port,
                         identity=args.identity, remote_dir=args.remote_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="action", required=True)
    backup_parser = commands.add_parser("backup", help="Create a compressed local database dump")
    backup_parser.add_argument("--output", type=Path, required=True)
    restore_parser = commands.add_parser("restore", help="Restore a dump into the VM's Postgres")
    restore_parser.add_argument("--input", type=Path, required=True)
    restore_parser.add_argument("--host", default=os.environ.get("NCS_SSH_HOST"),
                                help="VM IP/hostname (default: $NCS_SSH_HOST)")
    restore_parser.add_argument("--user", default=os.environ.get("NCS_SSH_USER") or os.environ.get("USER", "root"))
    restore_parser.add_argument("--port", type=int, default=int(os.environ.get("NCS_SSH_PORT", "22")))
    restore_parser.add_argument("--identity", default=os.environ.get("NCS_SSH_KEY"))
    restore_parser.add_argument("--remote-dir", default=deploy.REMOTE_DIR_DEFAULT)
    args = parser.parse_args()
    if args.action == "backup":
        backup(args.output)
    else:
        restore(args.input, _target(args))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
