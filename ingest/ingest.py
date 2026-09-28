"""Versioned ingest: NCS product PDFs + solutions -> pgvector.

Auto-discovers every version under data/NCS-* (e.g. NCS-25-7 -> version "25.7").
For each version dir:
    pdfs/*.pdf         -> source_type=doc      (product documentation)
    solutions/*.pdf    -> source_type=solution (known fixes, prose)
    solutions/*.csv    -> source_type=solution (one row per fix)

Idempotent by content hash: each source ("<version>/<filename>") is recorded in
`ingested_source` with a sha256; unchanged sources are skipped, changed ones are
dropped and re-embedded. INGEST_RESET=1 wipes the corpus first.

CSV schema (columns): version, symptom, cause, resolution, ticket_id.
The folder decides the version unless a row's `version` column overrides it.
"""
import csv
import glob
import hashlib
import io
import json
import os
import re

import psycopg
from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from embeddings import embed_batch, ensure_model

DATA = os.environ.get("DATA_DIR", "/app/data")
_SPLITTER = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)

# Boilerplate that repeats on every NCS/TSN page and, unstripped, gets embedded
# and FTS-indexed as if it were content — diluting both retrieval arms. Match
# only unambiguous lines (running "DN<id> <issue>" footer, Nokia legal notices,
# lone TOC dot-leaders) so real prose is never dropped.
_BOILERPLATE = re.compile(
    r"^\s*(?:"
    r"DN\d+\s+\d+-\d+"                                # running footer: "DN1000291295 1-0"
    r"|©?\s*\d{4}\s+Nokia\b.*"                        # copyright line
    r"|Nokia Confidential Information.*"              # confidentiality notice
    r"|NO WARRANTY OF ANY KIND.*"                     # start of the legal block
    r"|.*\.{5,}\s*\d*\s*"                             # TOC dot-leader rows
    r")\s*$",
    re.IGNORECASE,
)


def clean_page(text: str) -> str:
    return "\n".join(ln for ln in (text or "").splitlines()
                     if not _BOILERPLATE.match(ln))


def conn():
    return psycopg.connect(
        host=os.environ.get("PGHOST", "db"), port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def version_from_dir(path: str) -> str:
    # data/NCS-25-11 -> "25.11" ; data/NCS-25-7 -> "25.7"
    return os.path.basename(path).removeprefix("NCS-").replace("-", ".")


def title_from_file(filename: str, version: str) -> str:
    name = os.path.splitext(filename)[0]
    for tag in (f"NCS{version.replace('.', '_')}-", f"NCS{version.replace('.', '_')}_",
                f"NCS-{version}-", "NCS-", "C5_"):
        name = name.replace(tag, "")
    return name.replace("_", " ").replace("-", " ").strip()


def ensure_schema(cur):
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ingested_source (
            source TEXT PRIMARY KEY, kind TEXT NOT NULL, sha256 TEXT NOT NULL,
            chunks INT NOT NULL DEFAULT 0, ingested_at TIMESTAMPTZ DEFAULT now())""")


def seen(cur, source, digest) -> bool:
    cur.execute("SELECT sha256 FROM ingested_source WHERE source=%s", (source,))
    row = cur.fetchone()
    return bool(row and row[0] == digest)


def mark(cur, source, kind, digest, chunks):
    cur.execute(
        "INSERT INTO ingested_source (source, kind, sha256, chunks) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (source) DO UPDATE SET kind=EXCLUDED.kind, sha256=EXCLUDED.sha256, "
        "chunks=EXCLUDED.chunks, ingested_at=now()",
        (source, kind, digest, chunks),
    )


def drop_chunks(cur, source):
    cur.execute("DELETE FROM doc_chunk WHERE metadata->>'source' = %s", (source,))


def _vec(v) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def write_chunks(cur, rows: list[tuple[str, dict]], source: str):
    """rows = [(text, extra_meta), ...]. Embeds in batch and inserts."""
    texts = [t.replace("\x00", "") for t, _ in rows]  # PG rejects NUL; PDFs leak them
    metas = [m for _, m in rows]
    vecs = embed_batch(texts, task="search_document") if texts else []
    for text, meta, emb in zip(texts, metas, vecs):
        cur.execute(
            "INSERT INTO doc_chunk (text, source_url, metadata, embedding) "
            "VALUES (%s,%s,%s::jsonb,%s::vector)",
            (text, meta.get("source_url", ""), json.dumps({**meta, "source": source}), _vec(emb)),
        )


def load_pdf(cur, path, version, source_type):
    raw = open(path, "rb").read()
    fname = os.path.basename(path)
    source = f"{version}/{fname}"
    digest = sha(raw)
    if seen(cur, source, digest):
        print(f"unchanged: {source}"); return
    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as e:
        print(f"skip {source}: {e}"); return
    title = title_from_file(fname, version)
    drop_chunks(cur, source)
    rows: list[tuple[str, dict]] = []
    for pageno, page in enumerate(reader.pages, 1):
        for piece in _SPLITTER.split_text(clean_page(page.extract_text())):
            if piece.strip():
                rows.append((piece, {"version": version, "source_type": source_type,
                                     "title": title, "page": pageno}))
    write_chunks(cur, rows, source)
    mark(cur, source, source_type, digest, len(rows))
    print(f"{source_type} loaded: {source} ({len(rows)} chunks)")


# --- TSN-aware structured ingest --------------------------------------------
# Nokia Technical Support Notes follow a fixed template. Parsing it lets us keep
# the "Solution and correction instructions" retrievable as its own unit, tag the
# TSN/Jira ids, and fold the keywords into the indexed text — instead of blindly
# chunking the whole PDF (see docs/adr/0006). Best-effort: any PDF that doesn't
# parse as a TSN falls back to load_pdf's blind path.
_TSN_ID_RE = re.compile(r"TSN-NCS-SW-\d+", re.I)
_JIRA_RE = re.compile(r"\b(?:NCSFM|NCSDEV|CB)-\d+\b")
_HEADING_RE = re.compile(r"^\s*\d+(?:\.\d+)?\s+([A-Z].{2,58})$")


def _tsn_sections(cleaned: str) -> dict[str, str]:
    """Slice cleaned TSN text into {heading_lower: body} by its numbered headings."""
    lines = cleaned.splitlines()
    heads = [(i, m.group(1).strip().lower())
             for i, ln in enumerate(lines) if (m := _HEADING_RE.match(ln))]
    out: dict[str, str] = {}
    for j, (i, title) in enumerate(heads):
        end = heads[j + 1][0] if j + 1 < len(heads) else len(lines)
        out.setdefault(title, "\n".join(lines[i + 1:end]).strip())
    return out


def _pick(sections: dict[str, str], *prefixes: str) -> str:
    for p in prefixes:
        for title, body in sections.items():
            if title.startswith(p):
                return body
    return ""


def load_tsn(cur, path, version) -> bool:
    """Parse a TSN into structured solution chunks. Returns False (no writes) if
    the PDF isn't a recognizable TSN, so the caller can fall back to blind ingest."""
    raw = open(path, "rb").read()
    fname = os.path.basename(path)
    source = f"{version}/{fname}"
    digest = sha(raw)
    if seen(cur, source, digest):
        print(f"unchanged: {source}"); return True
    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception:
        return False
    full = "\n".join(clean_page(p.extract_text()) for p in reader.pages)
    tsn = _TSN_ID_RE.search(full)
    sections = _tsn_sections(full)
    desc = _pick(sections, "detailed description")
    solution = _pick(sections, "solution and correction")
    if not tsn or not (desc or solution):
        return False  # not a TSN we can parse → caller uses the blind path
    tsn_id = tsn.group(0).upper()
    keywords = _pick(sections, "keywords")
    purpose = (_pick(sections, "purpose") or fname).splitlines()
    title = f"{tsn_id}: {(purpose[0] if purpose else fname)[:80].strip()}"
    jira = list(dict.fromkeys(_JIRA_RE.findall(_pick(sections, "related jira") or full)))
    base = {"version": version, "source_type": "solution", "title": title,
            "tsn_id": tsn_id, "ticket_id": ", ".join(jira), "keywords": keywords[:500]}
    # symptom/cause context (keywords folded in so they're indexed) vs. the fix.
    symptom_block = "\n".join(filter(None, [
        f"Keywords: {keywords}" if keywords else "",
        _pick(sections, "executive summary"), _pick(sections, "network impact"), desc]))
    drop_chunks(cur, source)
    rows: list[tuple[str, dict]] = []
    for label, body in (("description", symptom_block), ("solution", solution)):
        for piece in _SPLITTER.split_text(body or ""):
            if piece.strip():
                rows.append((f"[{tsn_id}] {label}: {piece}", {**base, "section": label}))
    write_chunks(cur, rows, source)
    mark(cur, source, "solution", digest, len(rows))
    print(f"tsn loaded: {source} [{tsn_id}] ({len(rows)} chunks, jira={base['ticket_id'] or '-'})")
    return True


def load_solutions_csv(cur, path, version):
    raw = open(path, "rb").read()
    fname = os.path.basename(path)
    source = f"{version}/{fname}"
    digest = sha(raw)
    if seen(cur, source, digest):
        print(f"unchanged: {source}"); return
    drop_chunks(cur, source)
    rows: list[tuple[str, dict]] = []
    for r in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
        symptom = (r.get("symptom") or "").strip()
        text = (f"Symptom: {symptom}\n"
                f"Cause: {(r.get('cause') or '').strip()}\n"
                f"Resolution: {(r.get('resolution') or '').strip()}")
        rows.append((text, {"version": (r.get("version") or "").strip() or version,
                            "source_type": "solution", "title": symptom or "solution",
                            "ticket_id": (r.get("ticket_id") or "").strip()}))
    write_chunks(cur, rows, source)
    mark(cur, source, "solution", digest, len(rows))
    print(f"solution loaded: {source} ({len(rows)} rows)")


def load_version(cur, vdir):
    version = version_from_dir(vdir)
    print(f"== version {version} ({vdir}) ==")
    for p in sorted(glob.glob(f"{vdir}/pdfs/*.pdf")):
        load_pdf(cur, p, version, "doc")
    for p in sorted(glob.glob(f"{vdir}/solutions/*.pdf")):
        if not load_tsn(cur, p, version):       # structured TSN parse …
            load_pdf(cur, p, version, "solution")  # … else blind solution ingest
    for p in sorted(glob.glob(f"{vdir}/solutions/*.csv")):
        load_solutions_csv(cur, p, version)


def main():
    vdirs = sorted(d for d in glob.glob(f"{DATA}/NCS-*") if os.path.isdir(d))
    if not vdirs:
        print(f"no version dirs found under {DATA}/NCS-*"); return
    print("versions on disk:", ", ".join(version_from_dir(d) for d in vdirs))
    ensure_model()
    with conn() as c, c.cursor() as cur:
        ensure_schema(cur)
        if os.environ.get("INGEST_RESET") == "1":
            cur.execute("TRUNCATE doc_chunk, ingested_source RESTART IDENTITY")
            print("reset: corpus cleared")
        for vdir in vdirs:
            load_version(cur, vdir)
        c.commit()
    print("ingest complete")


def _selfcheck():
    sample = ("1 Purpose\nwhy\n6 Keywords\nfoo, bar\n9 Detailed description\nthe symptom\n"
              "10 Solution and correction instructions\ndo X then Y\n11 Related Jira ID\nNCSFM-1")
    s = _tsn_sections(sample)
    assert _pick(s, "detailed description") == "the symptom", s
    assert _pick(s, "solution and correction") == "do X then Y", s
    assert _pick(s, "keywords") == "foo, bar", s
    assert _JIRA_RE.findall(_pick(s, "related jira")) == ["NCSFM-1"]
    assert clean_page("real line\nDN1000291295 1-0\n©2026 Nokia.") == "real line"
    print("ok: TSN section parser + clean_page self-check passed")


if __name__ == "__main__":
    _selfcheck() if os.environ.get("INGEST_SELFCHECK") else main()
