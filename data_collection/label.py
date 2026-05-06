"""
Label, cross-reference, and classify papers in all_papers.csv.

Steps:
  crossref  -- add in_aea / in_jpal / rct_confirmed columns (no API calls)
  classify  -- LLM classification of unverified papers via OpenAI API
  export    -- write verified_papers.csv and needs_review.csv

Usage:
  python data_collection/label.py --step crossref
  python data_collection/label.py --step classify --limit 500
  python data_collection/label.py --step export
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import pandas as pd

from .config import CSV_PATH, DATA_DIR

VERIFIED_CSV  = DATA_DIR / "papers" / "verified_papers.csv"
REVIEW_CSV    = DATA_DIR / "papers" / "needs_review.csv"
LABEL_CACHE   = DATA_DIR / "papers" / ".label_cache.jsonl"   # one JSON line per classified paper


# ── Step 1: Cross-reference ────────────────────────────────────────────────

def _normalize_doi(doi: str | float | None) -> str:
    if not doi or str(doi).strip() in ("", "nan"):
        return ""
    doi = str(doi).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/",
                   "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi.strip()


def run_crossref(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["_doi_norm"] = df["doi"].apply(_normalize_doi)

    aea_dois  = set(df.loc[df["source"] == "aea_registry", "_doi_norm"].replace("", None).dropna())
    jpal_dois = set(df.loc[df["source"] == "jpal",         "_doi_norm"].replace("", None).dropna())

    df["in_aea"]  = (df["source"] == "aea_registry") | df["_doi_norm"].isin(aea_dois)
    df["in_jpal"] = (df["source"] == "jpal")          | df["_doi_norm"].isin(jpal_dois)
    df["rct_confirmed"] = df["in_aea"] | df["in_jpal"]

    if "has_rct_abstract" not in df.columns:
        df["has_rct_abstract"] = pd.NA
    if "rct_confidence" not in df.columns:
        df["rct_confidence"] = pd.NA
    if "rct_reason" not in df.columns:
        df["rct_reason"] = pd.NA

    df.drop(columns=["_doi_norm"], inplace=True)
    return df


def step_crossref() -> None:
    df = pd.read_csv(CSV_PATH, dtype=str)
    print(f"Loaded {len(df)} papers from {CSV_PATH.name}")

    df = run_crossref(df)

    total       = len(df)
    oa          = df["source"] == "openalex"
    confirmed   = df["rct_confirmed"] == "True"   # after round-trip through CSV it may be string
    # handle both bool and string representations
    df["rct_confirmed"] = df["rct_confirmed"].map(
        lambda x: x if isinstance(x, bool) else str(x).lower() == "true"
    )
    df["in_aea"]  = df["in_aea"].map(lambda x: x if isinstance(x, bool) else str(x).lower() == "true")
    df["in_jpal"] = df["in_jpal"].map(lambda x: x if isinstance(x, bool) else str(x).lower() == "true")

    n_aea_confirmed   = int(df["in_aea"].sum())
    n_jpal_confirmed  = int(df["in_jpal"].sum())
    n_confirmed       = int(df["rct_confirmed"].sum())
    n_oa_total        = int((df["source"] == "openalex").sum())
    n_oa_unverified   = int(((df["source"] == "openalex") & ~df["rct_confirmed"]).sum())
    n_oa_has_abstract = int(
        ((df["source"] == "openalex") & ~df["rct_confirmed"] & df["abstract"].notna()).sum()
    )

    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    print(f"Saved updated CSV with label columns.")
    print()
    print("=== Cross-reference Summary ===")
    print(f"  Total papers            : {total}")
    print(f"  Confirmed in AEA        : {n_aea_confirmed}")
    print(f"  Confirmed in J-PAL      : {n_jpal_confirmed}")
    print(f"  rct_confirmed (either)  : {n_confirmed}")
    print(f"  OpenAlex journal papers : {n_oa_total}")
    print(f"  OpenAlex NOT confirmed  : {n_oa_unverified}")
    print(f"    of which have abstract: {n_oa_has_abstract}  ← candidate pool for LLM classification")
    print()
    print(f"→ {n_oa_unverified} OpenAlex papers are NOT confirmed in AEA or J-PAL.")
    print(f"  Provide an OpenAI API key to classify the {n_oa_has_abstract} that have abstracts.")


# ── Step 2: LLM classification ─────────────────────────────────────────────

CLASSIFY_PROMPT = """\
You are screening academic papers to identify field experiments.

Title: {title}
{abstract_block}
Does this paper REPORT THE RESULTS of a randomized controlled trial or \
field experiment (i.e., the authors actually ran an experiment, not just \
cited or reviewed one)?

Reply with JSON only:
{{"answer": "yes" or "no", "confidence": 0.0-1.0, "reason": "one sentence"}}"""


def _load_label_cache() -> dict[str, dict]:
    cache: dict[str, dict] = {}
    if LABEL_CACHE.exists():
        for line in LABEL_CACHE.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
                cache[entry["paper_id"]] = entry
            except (json.JSONDecodeError, KeyError):
                pass
    return cache


def _save_to_cache(entry: dict) -> None:
    with LABEL_CACHE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def _classify_one(client, paper_id: str, title: str, abstract: str) -> dict:
    from openai import AsyncOpenAI  # imported here so the module loads without openai installed
    ab = abstract.strip()
    abstract_block = f"Abstract: {ab[:2000]}\n" if ab else "(No abstract — classify from title only)\n"
    prompt = CLASSIFY_PROMPT.format(title=title[:500], abstract_block=abstract_block)
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=120,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        answer     = parsed.get("answer", "").lower()
        confidence = float(parsed.get("confidence", 0.5))
        reason     = parsed.get("reason", "")

        if answer == "yes" and confidence >= 0.8:
            label = "llm_yes"
        elif answer == "no" and confidence >= 0.8:
            label = "llm_no"
        else:
            label = "needs_review"

        return {
            "paper_id": paper_id,
            "has_rct_abstract": label,
            "rct_confidence": confidence,
            "rct_reason": reason,
        }
    except Exception as exc:
        return {
            "paper_id": paper_id,
            "has_rct_abstract": "needs_review",
            "rct_confidence": None,
            "rct_reason": f"error: {exc}",
        }


async def _classify_batch(rows: list[dict], api_key: str, concurrency: int = 10) -> list[dict]:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)
    results: list[dict] = []
    for i in range(0, len(rows), concurrency):
        batch = rows[i: i + concurrency]
        tasks = [
            _classify_one(client, r["paper_id"], r["title"], r["abstract"])
            for r in batch
        ]
        batch_results = await asyncio.gather(*tasks)
        results.extend(batch_results)
        for entry in batch_results:
            _save_to_cache(entry)
        await asyncio.sleep(0.1)
        done = i + len(batch)
        if done % 100 == 0 or done == len(rows):
            print(f"    ... {done}/{len(rows)} classified")
    return results


def step_classify(limit: int, api_key_arg: str = "") -> None:
    api_key = api_key_arg or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: set OPENAI_API_KEY environment variable or pass --api-key.")
        return

    df = pd.read_csv(CSV_PATH, dtype=str)
    for col in ("rct_confirmed", "in_aea", "in_jpal"):
        if col in df.columns:
            df[col] = df[col].map(lambda x: str(x).lower() == "true")

    cache = _load_label_cache()
    print(f"Label cache: {len(cache)} entries")

    # Classify ALL unconfirmed OpenAlex papers (kw_strong, kw_weak, and kw_none)
    candidates = df[
        (df["source"] == "openalex")
        & (~df["rct_confirmed"])
        & df["abstract"].notna()
        & df["abstract"].str.strip().ne("")
    ].copy()

    # For title-only papers (no abstract): classify on title alone
    title_only = df[
        (df["source"] == "openalex")
        & (~df["rct_confirmed"])
        & (df["abstract"].isna() | df["abstract"].str.strip().eq(""))
    ].copy()
    title_only["abstract"] = ""   # blank abstract → LLM uses title only

    candidates = pd.concat([candidates, title_only], ignore_index=True)
    candidates["citation_count_n"] = pd.to_numeric(candidates["citation_count"], errors="coerce").fillna(0)
    candidates.sort_values("citation_count_n", ascending=False, inplace=True)

    to_classify = [
        {"paper_id": row["paper_id"], "title": row["title"], "abstract": row["abstract"]}
        for _, row in candidates.iterrows()
        if row["paper_id"] not in cache
    ][:limit]

    print(f"  Candidates (kw_strong+kw_weak): {len(candidates)}")
    print(f"  Already cached: {len(candidates) - len(to_classify)}")
    print(f"  Sending {len(to_classify)} papers to GPT-4o-mini")

    if to_classify:
        results = asyncio.run(_classify_batch(to_classify, api_key))
        for entry in results:
            cache[entry["paper_id"]] = entry

    # Apply cache back to dataframe
    for pid, entry in cache.items():
        mask = df["paper_id"] == pid
        if mask.any():
            df.loc[mask, "has_rct_abstract"] = entry.get("has_rct_abstract")
            df.loc[mask, "rct_confidence"]   = entry.get("rct_confidence")
            df.loc[mask, "rct_reason"]       = entry.get("rct_reason")

    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")

    llm_yes = int((df["has_rct_abstract"] == "llm_yes").sum())
    llm_no  = int((df["has_rct_abstract"] == "llm_no").sum())
    review  = int((df["has_rct_abstract"] == "needs_review").sum())
    print(f"  llm_yes={llm_yes}  llm_no={llm_no}  needs_review={review}")
    print("Saved updated CSV.")


# ── Step 3: Export ─────────────────────────────────────────────────────────

def step_export() -> None:
    df = pd.read_csv(CSV_PATH, dtype=str)
    for col in ("rct_confirmed", "in_aea", "in_jpal"):
        if col in df.columns:
            df[col] = df[col].map(lambda x: str(x).lower() == "true")

    verified = df[
        df["rct_confirmed"]
        | (df["has_rct_abstract"] == "llm_yes")
        | (df["has_rct_abstract"] == "needs_review")
    ].copy()

    review = df[df["has_rct_abstract"] == "needs_review"].copy()

    verified.to_csv(VERIFIED_CSV, index=False, encoding="utf-8-sig")
    review.to_csv(REVIEW_CSV, index=False, encoding="utf-8-sig")

    print(f"Exported {len(verified)} verified papers → {VERIFIED_CSV.name}")
    print(f"Exported {len(review)} needs-review papers → {REVIEW_CSV.name}")


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Label and classify papers")
    parser.add_argument("--step", choices=["crossref", "classify", "export"], required=True)
    parser.add_argument("--limit", type=int, default=2000,
                        help="Max papers to send to LLM in classify step")
    parser.add_argument("--api-key", default="", help="OpenAI API key")
    args = parser.parse_args()

    if args.step == "crossref":
        step_crossref()
    elif args.step == "classify":
        step_classify(args.limit, args.api_key)
    elif args.step == "export":
        step_export()


if __name__ == "__main__":
    main()
