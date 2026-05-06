"""
Fetch pre-registered field experiments from the AEA RCT Registry.

Strategy (no Selenium required):
  1. Paginate through the server-rendered search listing
     (search[status]=completed, 30 results per page) to collect trial IDs.
  2. Fetch each trial detail page with plain requests — pages are server-rendered.
  3. Parse title, registration date, abstract, PI, RCT ID, and linked paper DOIs.
  4. Filter by registration date >= year_from.
"""
from __future__ import annotations
import datetime
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..config import AEA_BASE_URL, AEA_PAGE_SLEEP
from ..schema import PaperRecord
from ..storage import append_manifest, load_raw_json_by_label, normalize_doi, save_raw_json

TODAY = datetime.date.today().isoformat()
SEARCH_URL = "https://www.socialscienceregistry.org/trials/search"
TRIAL_URL  = "https://www.socialscienceregistry.org/trials/{tid}"

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
})

_MONTH_MAP = {m: i for i, m in enumerate(
    ["january","february","march","april","may","june",
     "july","august","september","october","november","december"], 1
)}


def _parse_date(text: str) -> Optional[datetime.date]:
    """Parse dates like 'March 22, 2016' or 'June 11, 2020'."""
    m = re.search(r"(\w+)\s+(\d{1,2}),\s+(\d{4})", text or "")
    if not m:
        return None
    month = _MONTH_MAP.get(m.group(1).lower())
    if not month:
        return None
    try:
        return datetime.date(int(m.group(3)), month, int(m.group(2)))
    except ValueError:
        return None


def _get_listing_page(page: int) -> list[str]:
    """Fetch one search results page, return list of unique trial IDs."""
    params = {"search[status]": "completed", "page": page}
    try:
        r = _SESSION.get(SEARCH_URL, params=params, timeout=20)
        r.raise_for_status()
    except requests.RequestException:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    ids = list(dict.fromkeys(
        m.group(1)
        for a in soup.find_all("a", href=True)
        for m in [re.match(r"^/trials/(\d+)$", a["href"])]
        if m
    ))
    return ids


def _parse_trial_page(html: str, tid: str) -> dict:
    """Extract all fields from a trial detail page."""
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""

    # RCT ID: AEARCTR-XXXXXXX
    rct_id = ""
    m = re.search(r"AEARCTR-\d+", html)
    if m:
        rct_id = m.group()

    # Registration date: find a div containing exactly "Initial registration date"
    # then take the next sibling div for the value
    reg_date = None
    for div in soup.find_all("div"):
        t = div.get_text(strip=True)
        if t == "Initial registration date":
            sib = div.find_next_sibling("div")
            if sib:
                reg_date = _parse_date(sib.get_text(strip=True))
            break

    # Abstract: find div whose text starts with "Abstract" and contains substance
    abstract = ""
    for div in soup.find_all("div"):
        t = div.get_text(" ", strip=True)
        if t.startswith("Abstract") and len(t) > 30:
            abstract = t[len("Abstract"):].strip()[:3000]
            if abstract:
                break

    # Primary investigator name
    pi_name = ""
    for div in soup.find_all("div"):
        if div.get_text(strip=True) == "Name":
            sib = div.find_next_sibling("div")
            if sib:
                pi_name = sib.get_text(strip=True)
                break

    # Linked journal paper DOIs (exclude the registry's own DOI for this trial)
    paper_dois = list(dict.fromkeys(
        href
        for a in soup.find_all("a", href=True)
        for href in [a["href"]]
        if href.startswith("https://doi.org/10.")
        and "/rct." not in href
    ))

    # Country
    country = ""
    for div in soup.find_all("div"):
        if div.get_text(strip=True) == "Country":
            sib = div.find_next_sibling("div")
            if sib:
                country = sib.get_text(strip=True)
                break

    return {
        "tid": tid,
        "rct_id": rct_id or f"AEARCTR-{tid.zfill(7)}",
        "title": title,
        "abstract": abstract,
        "pi_name": pi_name,
        "reg_date": reg_date.isoformat() if reg_date else None,
        "country": country,
        "paper_dois": paper_dois,
        "url": TRIAL_URL.format(tid=tid),
    }


def _to_record(raw: dict, year_from: int) -> Optional[PaperRecord]:
    if not raw.get("title"):
        return None
    reg_date_str = raw.get("reg_date")
    if reg_date_str:
        year = int(reg_date_str[:4])
        if year < year_from:
            return None

    # Primary DOI: first linked journal paper DOI if any; else None
    paper_dois = raw.get("paper_dois", [])
    doi = normalize_doi(paper_dois[0]) if paper_dois else None

    return PaperRecord(
        paper_id=f"AEA_{raw['rct_id']}",
        doi=doi,
        title=raw["title"],
        authors_str=raw.get("pi_name", ""),
        year=int(raw["reg_date"][:4]) if raw.get("reg_date") else None,
        journal=None,
        source="aea_registry",
        abstract=raw.get("abstract") or None,
        url=raw["url"],
        pdf_url=None,
        rct_registry_id=raw["rct_id"],
        jpal_id=None,
        citation_count=None,
        date_collected=TODAY,
    )


def collect_aea(year_from: int, manifest: set[str]) -> list[PaperRecord]:
    print("  [aea_rct] Collecting AEA RCT Registry (completed trials)...")

    # Step 1: collect all trial IDs from listing pages
    all_ids: list[str] = []
    page = 1
    while True:
        listing_key = f"aea_listing_p{page:04d}"
        if listing_key in manifest:
            cached = load_raw_json_by_label("aea_rct", listing_key)
            ids = cached.get("ids", []) if cached else []
        else:
            ids = _get_listing_page(page)
            if not ids:
                break
            save_raw_json("aea_rct", listing_key, {"page": page, "ids": ids})
            append_manifest(listing_key)
            time.sleep(AEA_PAGE_SLEEP)

        if not ids:
            break
        all_ids.extend(ids)
        page += 1

    unique_ids = list(dict.fromkeys(all_ids))
    print(f"    Found {len(unique_ids)} completed trials across {page-1} listing pages")

    # Step 2: fetch and parse each trial detail page
    records: list[PaperRecord] = []
    skipped_year = 0
    for i, tid in enumerate(unique_ids, 1):
        detail_key = f"aea_trial_{tid}"
        if detail_key in manifest:
            raw = load_raw_json_by_label("aea_rct", detail_key)
        else:
            try:
                r = _SESSION.get(TRIAL_URL.format(tid=tid), timeout=15)
                r.raise_for_status()
                raw = _parse_trial_page(r.text, tid)
                save_raw_json("aea_rct", detail_key, raw)
                append_manifest(detail_key)
            except requests.RequestException as exc:
                print(f"    [error] trial/{tid}: {exc}")
                raw = None
            time.sleep(AEA_PAGE_SLEEP)

        if raw:
            rec = _to_record(raw, year_from)
            if rec:
                records.append(rec)
            elif raw.get("reg_date") and int(raw["reg_date"][:4]) < year_from:
                skipped_year += 1

        if i % 100 == 0:
            print(f"    ... {i}/{len(unique_ids)} processed, {len(records)} kept")

    print(f"    -> {len(records)} records (skipped {skipped_year} pre-{year_from})")
    return records
