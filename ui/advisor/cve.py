"""Generic (vendor-neutral) CVE lookup via NVD REST 2.0. Best-effort."""
import os
import requests

NVD = "https://services.nvd.nist.gov/rest/json/cves/2.0"
_KEY = os.environ.get("NVD_API_KEY")  # ponytail: no key; add NVD_API_KEY when 429s appear
_HEADERS = {"apiKey": _KEY} if _KEY else {}


def _slim(cve: dict) -> dict:
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
    return {"id": m.get("id"), "summary": summary, "cvss": score, "severity": severity,
            "url": f"https://nvd.nist.gov/vuln/detail/{m.get('id')}"}


def fetch_cve(cve_id: str) -> dict | None:
    try:
        r = requests.get(NVD, params={"cveId": cve_id}, headers=_HEADERS, timeout=20)
        r.raise_for_status()
        items = r.json().get("vulnerabilities", [])
        return _slim(items[0]) if items else None
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
