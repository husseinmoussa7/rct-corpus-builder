"""Fetch field experiment publications from the Behavioural Insights Team."""
from __future__ import annotations
import datetime
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..config import BIT_BASE_URL, BIT_PAGE_SLEEP
from ..schema import PaperRecord
from ..storage import append_manifest, normalize_doi, save_raw_json

TODAY = datetime.date.today().isoformat()

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; FieldExperimentCollector/1.0)",
    "Accept-Language": "en-US,en;q=0.9",
})

_RCT_TAGS = {"randomised controlled trial", "randomized controlled trial", "rct",
             "field experiment", "field study", "experiment"}


def _extract_year(text: str) -> Optional[int]:
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group()) if m else None


def _slug_id(url: str) -> str:
    clean = re.sub(r"[^\w]+", "-", url.strip("/").split("/")[-1])
    return clean[:60]


def _parse_publication_card(card, base_url: str) -> Optional[dict]:
    """Extract minimal fields from a BIT publication card."""
    a_tag = card.find("a", href=True)
    if not a_tag:
        return None
    href = a_tag["href"]
    if not href.startswith("http"):
        href = "https://www.bi.team" + href
    title_tag = card.find(["h2", "h3", "h4"])
    title = title_tag.get_text(strip=True) if title_tag else a_tag.get_text(strip=True)
    if not title:
        return None

    date_tag = card.find(class_=re.compile(r"date|time|year", re.I))
    date_text = date_tag.get_text(strip=True) if date_tag else card.get_text()
    year = _extract_year(date_text)

    tags_text = card.get_text(" ").lower()
    is_rct = any(tag in tags_text for tag in _RCT_TAGS)

    return {"title": title, "url": href, "year": year, "is_rct": is_rct}


def _fetch_detail(url: str) -> dict:
    try:
        resp = _SESSION.get(url, timeout=20)
        resp.raise_for_status()
    except requests.RequestException:
        return {}
    soup = BeautifulSoup(resp.text, "lxml")
    abstract = ""
    for sel in ["div.entry-content", "div.publication-content",
                "div.field-item", "article", "main"]:
        tag = soup.select_one(sel)
        if tag:
            paragraphs = tag.find_all("p")
            abstract = " ".join(p.get_text(strip=True) for p in paragraphs[:4])
            break

    doi = ""
    doi_match = re.search(r"10\.\d{4,}/\S+", resp.text)
    if doi_match:
        doi = doi_match.group().rstrip(".,;)")

    authors_tag = soup.find(class_=re.compile(r"author", re.I))
    authors_str = authors_tag.get_text(strip=True) if authors_tag else ""

    tags_text = resp.text.lower()
    is_rct = any(tag in tags_text for tag in _RCT_TAGS)

    return {"abstract": abstract, "doi": doi, "authors_str": authors_str, "is_rct": is_rct}


def collect_bit(year_from: int, manifest: set[str]) -> list[PaperRecord]:
    print("  [bit] Collecting BIT publications...")
    records: list[PaperRecord] = []
    page_num = 1

    while True:
        url = f"{BIT_BASE_URL}?paged={page_num}" if page_num > 1 else BIT_BASE_URL
        manifest_key = f"bit_listing_p{page_num:04d}"

        try:
            resp = _SESSION.get(url, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"    [error] listing page {page_num}: {exc}")
            break

        soup = BeautifulSoup(resp.text, "lxml")
        cards = (
            soup.select("article.publication")
            or soup.select("div.publication")
            or soup.select("li.publication")
            or soup.select("div.views-row")
        )
        if not cards:
            break

        page_cards = []
        for card in cards:
            info = _parse_publication_card(card, BIT_BASE_URL)
            if info:
                page_cards.append(info)

        if not page_cards:
            break

        save_raw_json("bit", manifest_key, {"page": page_num, "cards": page_cards})
        append_manifest(manifest_key)
        time.sleep(BIT_PAGE_SLEEP)

        for info in page_cards:
            year = info.get("year")
            if year and year < year_from:
                continue

            detail_key = f"bit_detail_{_slug_id(info['url'])}"
            if detail_key not in manifest:
                detail = _fetch_detail(info["url"])
                if detail:
                    info.update(detail)
                save_raw_json("bit", detail_key, info)
                append_manifest(detail_key)
                time.sleep(BIT_PAGE_SLEEP)

            if not info.get("is_rct"):
                continue

            doi = normalize_doi(info.get("doi", ""))
            records.append(PaperRecord(
                paper_id=f"BIT_{_slug_id(info['url'])}",
                doi=doi or None,
                title=info["title"],
                authors_str=info.get("authors_str", ""),
                year=info.get("year"),
                journal=None,
                source="bit",
                abstract=info.get("abstract"),
                url=info["url"],
                pdf_url=None,
                rct_registry_id=None,
                jpal_id=None,
                citation_count=None,
                date_collected=TODAY,
            ))

        page_num += 1

    print(f"    -> {len(records)} BIT publications")
    return records
