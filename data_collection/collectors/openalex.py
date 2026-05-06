"""Fetch field experiment papers from academic journals via OpenAlex API."""
from __future__ import annotations
import datetime
import time
from typing import Optional

import requests

from ..config import (
    OPENALEX_BASE, OPENALEX_EMAIL, OPENALEX_PER_PAGE, OPENALEX_SLEEP,
    FIELD_EXPERIMENT_SEARCHES,
)
from ..schema import PaperRecord
from ..storage import append_manifest, load_raw_json_by_label, normalize_doi, save_raw_json

_SELECT = (
    "id,doi,title,authorships,publication_year,"
    "primary_location,abstract_inverted_index,open_access,cited_by_count"
)
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": f"FieldExperimentCollector/1.0 (mailto:{OPENALEX_EMAIL})"})

TODAY = datetime.date.today().isoformat()


def _reconstruct_abstract(inv: dict | None) -> Optional[str]:
    if not inv:
        return None
    pairs = [(pos, word) for word, positions in inv.items() for pos in positions]
    pairs.sort(key=lambda x: x[0])
    result = " ".join(w for _, w in pairs)
    return result or None


def _parse_work(work: dict, journal_abbr: str) -> PaperRecord:
    raw_doi = work.get("doi") or ""
    doi = normalize_doi(raw_doi)
    oa = work.get("open_access") or {}
    pdf_url = oa.get("oa_url")
    loc = work.get("primary_location") or {}
    url = loc.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else None)

    authors = "; ".join(
        a["author"]["display_name"]
        for a in work.get("authorships", [])
        if (a.get("author") or {}).get("display_name")
    )

    wid = (work.get("id") or "").split("/")[-1]
    return PaperRecord(
        paper_id=f"OA_{wid}",
        doi=doi or None,
        title=work.get("title") or "",
        authors_str=authors,
        year=work.get("publication_year"),
        journal=journal_abbr,
        source="openalex",
        abstract=_reconstruct_abstract(work.get("abstract_inverted_index")),
        url=url,
        pdf_url=pdf_url,
        rct_registry_id=None,
        jpal_id=None,
        citation_count=work.get("cited_by_count"),
        date_collected=TODAY,
    )


def _fetch_pages(
    issn: str,
    journal_abbr: str,
    search_term: str,
    year_from: int,
    manifest: set[str],
    seen_ids: set[str],
) -> list[PaperRecord]:
    """Paginate through OpenAlex for one (journal, search_term) pair."""
    records: list[PaperRecord] = []
    term_slug = search_term.replace(" ", "_")
    cursor = "*"
    page_num = 0

    while True:
        manifest_key = f"openalex_{journal_abbr}_{term_slug}_p{page_num:04d}"

        if manifest_key in manifest:
            raw = load_raw_json_by_label("openalex", manifest_key)
            if raw is None:
                print(f"    [warn] manifest hit but raw file missing: {manifest_key}")
                break
        else:
            params = {
                "filter": (
                    f"primary_location.source.issn:{issn},"
                    f"from_publication_date:{year_from}-01-01"
                ),
                "search": search_term,
                "per-page": OPENALEX_PER_PAGE,
                "cursor": cursor,
                "select": _SELECT,
                "mailto": OPENALEX_EMAIL,
            }
            try:
                resp = _SESSION.get(OPENALEX_BASE, params=params, timeout=30)
                resp.raise_for_status()
                raw = resp.json()
            except requests.RequestException as exc:
                print(f"    [error] {manifest_key}: {exc}")
                break
            save_raw_json("openalex", manifest_key, raw)
            append_manifest(manifest_key)
            time.sleep(OPENALEX_SLEEP)

        results = raw.get("results", [])
        if not results:
            break

        for work in results:
            wid = (work.get("id") or "").split("/")[-1]
            if wid and wid not in seen_ids:
                seen_ids.add(wid)
                records.append(_parse_work(work, journal_abbr))

        cursor = (raw.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
        page_num += 1

    return records


def fetch_journal(
    journal_abbr: str,
    issn: str,
    year_from: int,
    manifest: set[str],
) -> list[PaperRecord]:
    """Fetch field experiment papers from one journal using OpenAlex server-side search."""
    print(f"  [openalex] {journal_abbr} ({issn}), year >= {year_from}")
    records: list[PaperRecord] = []
    seen_ids: set[str] = set()  # dedup across all search terms by OpenAlex work ID

    for term in FIELD_EXPERIMENT_SEARCHES:
        new = _fetch_pages(issn, journal_abbr, term, year_from, manifest, seen_ids)
        print(f"    search='{term}' -> {len(new)} new papers")
        records.extend(new)

    print(f"    -> {len(records)} total (deduplicated)")
    return records


def fetch_all_journals(
    year_from: int,
    manifest: set[str],
    journals: dict[str, str],
) -> list[PaperRecord]:
    all_records: list[PaperRecord] = []
    for abbr, issn in journals.items():
        all_records.extend(fetch_journal(abbr, issn, year_from, manifest))
    return all_records
