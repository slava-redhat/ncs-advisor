"""Generic (vendor-neutral) CVE lookup via NVD REST 2.0. Best-effort."""
import os
import re
import requests

NVD = "https://services.nvd.nist.gov/rest/json/cves/2.0"
RED_HAT = "https://access.redhat.com/hydra/rest/securitydata/cve/{cve_id}.json"
_KEY = os.environ.get("NVD_API_KEY")  # ponytail: no key; add NVD_API_KEY when 429s appear
_HEADERS = {"apiKey": _KEY} if _KEY else {}
CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)


def extract_cve_ids(text: str) -> list[str]:
    """Return canonical CVE ids from arbitrary user text."""
    return list(dict.fromkeys(match.upper() for match in CVE_RE.findall(text or "")))


def _slim(cve: dict, red_hat: dict | None = None) -> dict:
    m = cve.get("cve", cve)
    descs = m.get("descriptions", [])
    summary = next((d["value"] for d in descs if d.get("lang") == "en"),
                   descs[0]["value"] if descs else "")
    score = severity = None
    metrics = m.get("metrics", {})
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        if metrics.get(key):
            data = metrics[key][0]["cvssData"]
            score = data.get("baseScore")
            severity = data.get("baseSeverity") or metrics[key][0].get("baseSeverity")
            break
    references = [
        ref.get("url") for ref in m.get("references", [])
        if ref.get("url")
    ]
    result = {
        "id": m.get("id"),
        "summary": summary,
        "cvss": score,
        "severity": severity,
        "url": f"https://nvd.nist.gov/vuln/detail/{m.get('id')}",
        "references": references[:12],
    }
    if red_hat:
        result.update({
            "red_hat_statement": red_hat.get("statement", ""),
            "red_hat_details": red_hat.get("details", []),
            "mitigation": (red_hat.get("mitigation") or {}).get("value", ""),
            "affected_releases": red_hat.get("affected_release", []),
            "package_states": red_hat.get("package_state", []),
            "red_hat_url": f"https://access.redhat.com/security/cve/{m.get('id')}",
        })
    return result


def _fetch_red_hat(cve_id: str) -> dict | None:
    try:
        r = requests.get(RED_HAT.format(cve_id=cve_id), timeout=20)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def fetch_cve(cve_id: str) -> dict | None:
    try:
        cve_id = cve_id.upper()
        r = requests.get(NVD, params={"cveId": cve_id}, headers=_HEADERS, timeout=20)
        r.raise_for_status()
        items = r.json().get("vulnerabilities", [])
        if not items:
            return None
        return _slim(items[0], _fetch_red_hat(cve_id))
    except Exception:
        return None


def search_cve(keyword: str, limit: int = 3) -> list[dict]:
    try:
        r = requests.get(NVD, params={"keywordSearch": keyword, "resultsPerPage": limit},
                         headers=_HEADERS, timeout=20)
        r.raise_for_status()
        return [_slim(v) for v in r.json().get("vulnerabilities", [])[:limit]]
    except Exception:
        return []
