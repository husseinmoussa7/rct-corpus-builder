from __future__ import annotations
import datetime
import json
import pathlib
import re
import unicodedata
from typing import Any

import pandas as pd

from .config import RAW_DIR, CSV_PATH, MANIFEST_PATH, LOG_PATH
from .schema import CSV_COLUMNS, PaperRecord

SOURCE_PRIORITY = {"openalex": 0, "aea_registry": 1, "jpal": 2, "bit": 3}


def ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for sub in ("openalex", "aea_rct", "jpal", "bit"):
        (RAW_DIR / sub).mkdir(exist_ok=True)


def normalize_doi(doi_raw: str | None) -> str:
    if not doi_raw:
        return ""
    doi = str(doi_raw).strip().lower()
    if doi == "nan":  # np.nan loaded from CSV via dtype=str becomes the string "nan"
        return ""
    for prefix in ("https://doi.org/", "http://doi.org/",
                   "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi.strip()


def normalize_title_key(title: str | None) -> str:
    if not title:
        return ""
    t = str(title).lower()
    if t == "nan":
        return ""
    t = unicodedata.normalize("NFKD", t)
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def save_raw_json(subdir: str, label: str, data: Any) -> pathlib.Path:
    ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    dest = RAW_DIR / subdir / f"{label}-{ts}.json"
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def load_raw_json_by_label(subdir: str, label: str) -> dict | None:
    matches = sorted((RAW_DIR / subdir).glob(f"{label}-*.json"))
    if not matches:
        return None
    return json.loads(matches[-1].read_text(encoding="utf-8"))


def load_manifest() -> set[str]:
    if not MANIFEST_PATH.exists():
        return set()
    return set(MANIFEST_PATH.read_text(encoding="utf-8").splitlines())


def append_manifest(key: str) -> None:
    with MANIFEST_PATH.open("a", encoding="utf-8") as f:
        f.write(key + "\n")


def load_existing_csv() -> pd.DataFrame:
    if CSV_PATH.exists():
        return pd.read_csv(CSV_PATH, dtype=str)
    return pd.DataFrame(columns=CSV_COLUMNS)


def save_csv(new_records: list[PaperRecord]) -> int:
    """Append new_records to the CSV, dedup by DOI then title+year. Returns count written."""
    existing_df = load_existing_csv()
    new_df = pd.DataFrame(
        [vars(r) for r in new_records],
        columns=CSV_COLUMNS,
    )
    combined = pd.concat([existing_df, new_df], ignore_index=True)

    combined["_doi_norm"]  = combined["doi"].apply(normalize_doi)
    combined["_title_key"] = (
        combined["title"].apply(normalize_title_key)
        + "_"
        + combined["year"].astype(str).replace("None", "0").replace("nan", "0")
    )
    combined["_src_pri"] = combined["source"].map(SOURCE_PRIORITY).fillna(99)
    combined.sort_values("_src_pri", inplace=True)

    has_doi = combined["_doi_norm"] != ""
    deduped_doi   = combined[has_doi].drop_duplicates(subset=["_doi_norm"], keep="first")
    no_doi_deduped = combined[~has_doi].drop_duplicates(subset=["_title_key"], keep="first")
    combined = pd.concat([deduped_doi, no_doi_deduped], ignore_index=True)

    combined.drop(columns=["_doi_norm", "_title_key", "_src_pri"], inplace=True)
    combined = combined.reindex(columns=CSV_COLUMNS)
    combined.sort_values(
        by=["year", "journal", "title"],
        ascending=[False, True, True],
        na_position="last",
        inplace=True,
    )
    combined.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    return len(new_records)


def update_log(counts: dict[str, int]) -> None:
    log: dict = {}
    if LOG_PATH.exists():
        try:
            log = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    log["last_run"] = datetime.datetime.utcnow().isoformat()
    by_source = log.setdefault("by_source", {})
    for k, v in counts.items():
        by_source[k] = by_source.get(k, 0) + v
    log["total_papers"] = sum(by_source.values())
    LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
