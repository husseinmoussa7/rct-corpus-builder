"""
Fetch evaluations from the J-PAL database.

Strategy (no Selenium, no broken API):
  1. Parse J-PAL's sitemap to get all /evaluation/* URLs (~1300 pages).
  2. Fetch each evaluation page with plain requests — Drupal/Gatsby pre-renders HTML.
  3. Extract title, abstract, researchers, year, DOI, node ID.
"""
from __future__ import annotations
import datetime
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..config import JPAL_PAGE_SLEEP
from ..schema import PaperRecord
from ..storage import append_manifest, load_raw_json_by_label, normalize_doi, save_raw_json

TODAY = datetime.date.today().isoformat()
SITEMAP_BASE = "https://www.povertyactionlab.org/sitemap.xml"

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
})

# Boilerplate paragraphs that appear on every J-PAL page — skip these
_BOILERPLATE = [
    "The Abdul Latif Jameel Poverty Action Lab",
    "Our affiliated professors are based at",
    "Our research, policy, and training work",
    "J-PAL's mission is to reduce poverty",
]


def _get_sitemap_page_count() -> int:
    """Return total number of sitemap pages from the sitemap index."""
    try:
        r = _SESSION.get(SITEMAP_BASE, timeout=15)
        r.raise_for_status()
        pages = re.findall(r"sitemap\.xml\?page=(\d+)", r.text)
        return max((int(p) for p in pages), default=1)
    except requests.RequestException:
        return 5  # fallback: scan up to 5 pages


def _get_evaluation_urls() -> list[str]:
    """Extract all /evaluation/* URLs from all J-PAL sitemap pages."""
    total_pages = _get_sitemap_page_count()
    urls: list[str] = []
    for page in range(1, total_pages + 1):
        try:
            r = _SESSION.get(f"{SITEMAP_BASE}?page={page}", timeout=15)
            r.raise_for_status()
        except requests.RequestException:
            continue
        found = re.findall(
            r"<loc>(https://www\.povertyactionlab\.org/evaluation/[^<]+)</loc>",
            r.text,
        )
        urls.extend(found)
    return list(dict.fromkeys(urls))  # deduplicate, preserve order


def _slug_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1][:80]


def _parse_eval_page(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""

    # Node ID from inline JSON script (Drupal path data)
    jpal_id = ""
    for script in soup.find_all("script", type="application/json"):
        if script.string and "currentPath" in script.string:
            m = re.search(r'"currentPath"\s*:\s*"node\\?/(\d+)"', script.string)
            if m:
                jpal_id = m.group(1)
                break

    # Abstract: first non-boilerplate paragraph longer than 80 chars
    abstract = ""
    for p in soup.find_all("p"):
        t = p.get_text(strip=True)
        if len(t) < 80:
            continue
        if any(t.startswith(bp) for bp in _BOILERPLATE):
            continue
        abstract = t[:3000]
        break

    # Researchers — look for names near "Researcher" or "Investigator" labels
    _NAV_NOISE = {"Invited Researchers", "Events", "About", "Research", "Policy",
                  "Training", "Publications", "Data", "Contact", "Search"}
    researchers: list[str] = []
    for el in soup.find_all(string=re.compile(r"Researcher|Investigator", re.I)):
        parent = el.find_parent()
        if not parent:
            continue
        container = parent.find_parent()
        if container:
            names = [
                a.get_text(strip=True)
                for a in container.find_all("a")
                if a.get_text(strip=True) and a.get_text(strip=True) not in _NAV_NOISE
                and len(a.get_text(strip=True)) > 3
            ]
            researchers.extend(names)
    researchers = list(dict.fromkeys(researchers))

    # Year — from any 4-digit year in the page text (prefer 201x/202x)
    year = None
    year_matches = re.findall(r"\b(201[0-9]|202[0-6])\b", html)
    if year_matches:
        from collections import Counter
        year = int(Counter(year_matches).most_common(1)[0][0])

    # DOI — any doi.org link that isn't the J-PAL page itself
    doi_links = list(dict.fromkeys(
        href
        for a in soup.find_all("a", href=True)
        for href in [a["href"]]
        if "doi.org/10." in href
    ))

    return {
        "title": title,
        "jpal_id": jpal_id,
        "abstract": abstract,
        "researchers": researchers,
        "year": year,
        "doi_links": doi_links,
        "url": url,
        "slug": _slug_from_url(url),
    }


def _to_record(raw: dict, year_from: int) -> Optional[PaperRecord]:
    if not raw.get("title"):
        return None
    year = raw.get("year")
    if year and year < year_from:
        return None
    doi = normalize_doi(raw["doi_links"][0]) if raw.get("doi_links") else None
    return PaperRecord(
        paper_id=f"JPAL_{raw['jpal_id'] or raw['slug']}",
        doi=doi,
        title=raw["title"],
        authors_str="; ".join(raw.get("researchers", [])),
        year=year,
        journal=None,
        source="jpal",
        abstract=raw.get("abstract") or None,
        url=raw["url"],
        pdf_url=None,
        rct_registry_id=None,
        jpal_id=raw.get("jpal_id") or None,
        citation_count=None,
        date_collected=TODAY,
    )


def collect_jpal(year_from: int, manifest: set[str]) -> list[PaperRecord]:
    print("  [jpal] Collecting J-PAL evaluations via sitemap...")

    sitemap_key = "jpal_sitemap"
    if sitemap_key in manifest:
        cached = load_raw_json_by_label("jpal", sitemap_key)
        eval_urls = cached.get("urls", []) if cached else []
    else:
        eval_urls = _get_evaluation_urls()
        save_raw_json("jpal", sitemap_key, {"urls": eval_urls})
        append_manifest(sitemap_key)

    print(f"    Found {len(eval_urls)} evaluation URLs in sitemap")

    records: list[PaperRecord] = []
    skipped = 0
    for i, url in enumerate(eval_urls, 1):
        slug = _slug_from_url(url)
        manifest_key = f"jpal_eval_{slug}"

        if manifest_key in manifest:
            raw = load_raw_json_by_label("jpal", manifest_key)
        else:
            try:
                r = _SESSION.get(url, timeout=15)
                r.raise_for_status()
                raw = _parse_eval_page(r.text, url)
                save_raw_json("jpal", manifest_key, raw)
                append_manifest(manifest_key)
            except requests.RequestException as exc:
                print(f"    [error] {slug}: {exc}")
                raw = None
            time.sleep(JPAL_PAGE_SLEEP)

        if raw:
            rec = _to_record(raw, year_from)
            if rec:
                records.append(rec)
            else:
                skipped += 1

        if i % 100 == 0:
            print(f"    ... {i}/{len(eval_urls)} processed, {len(records)} kept")

    print(f"    -> {len(records)} records (skipped {skipped} pre-{year_from} or no title)")
    return records
