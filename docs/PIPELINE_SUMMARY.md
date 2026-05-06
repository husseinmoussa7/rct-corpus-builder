# Field Experiment Paper Collection — Pipeline Summary
**Date:** May 6, 2026 | **Prepared for:** PI Xiao

---

## 1. What We Built

An automated pipeline that collects, deduplicates, labels, and classifies academic field experiment papers from four data sources into a single curated dataset. The pipeline is fully resumable — every network request is cached to disk, so re-running it skips already-fetched pages and only retrieves new content.

---

## 2. Data Sources

| Source | What it is | Records collected |
|--------|-----------|-------------------|
| **OpenAlex API** | 15 top academic journals searched for RCT/experiment keywords | 2,857 papers |
| **AEA RCT Registry** | All completed trials on socialscienceregistry.org | 3,616 trials |
| **J-PAL Evaluations** | All evaluations on povertyactionlab.org (scraped via sitemap) | 705 evaluations |
| **BIT** | Behavioural Insights Team publications | 0 (no structured listing found) |
| **Total (after dedup)** | | **7,178 records** |

### Journals covered (OpenAlex)
AER, QJE, JPE, RAND, RES, Econometrica, JDE, JHE, Marketing Science, Management Science, JMR, ISR, MISQ, SMJ, Organization Science — all publications from 2015 onward.

### Search terms used (OpenAlex)
"field experiment" · "randomized controlled trial" · "randomized evaluation" · "natural field experiment"

---

## 3. Pipeline Steps

```
Step 1 — Collect
  ├── OpenAlex API  → 2,857 journal papers (cached: ~65 JSON files)
  ├── AEA Registry  → 3,616 trial records (cached: ~4,140 JSON files)
  └── J-PAL sitemap → 705 evaluation pages (cached: ~1,270 JSON files)
                                    ↓
Step 2 — Deduplicate & merge  →  all_papers.csv  (7,178 rows)
  Primary key: normalized DOI  |  Fallback: normalized title + year

Step 3 — Cross-reference
  ├── in_aea  = True if source is AEA or DOI matches an AEA record
  └── in_jpal = True if source is J-PAL or DOI matches a J-PAL record
  → 4,321 registry-confirmed papers

Step 4 — Abstract recovery (for 331 missing abstracts)
  ├── Semantic Scholar batch API → recovered 150 abstracts
  └── Elsevier API (JDE/JHE)    → blocked without institutional IP
  → 181 still missing (all Elsevier journals)

Step 5 — Keyword filter  (on 2,857 unconfirmed OpenAlex papers)
  ├── kw_strong: 866 papers  — "randomized trial", "field experiment", "RCT", etc.
  ├── kw_weak:   407 papers  — "experiment", "intervention", "treatment effect", etc.
  └── kw_none: 1,584 papers  — no experiment language in title/abstract

Step 6 — LLM classification  (GPT-4o-mini, ~$0.23 total for all 2,857 papers)
  All 2,857 unconfirmed OpenAlex papers sent to GPT-4o-mini:
  ├── kw_strong + kw_weak (1,273): llm_yes=1,011  llm_no=262   needs_review=0
  └── kw_none (1,584):             llm_yes=168     llm_no=1,416 needs_review=0
  Total:                           llm_yes=1,179   llm_no=1,678 needs_review=0

Step 7 — Manual review & quality pass
  ├── Resolved 7 borderline cases (previously needs_review=0.7 confidence, no abstract):
  │     5 confirmed as field experiments  →  merged into verified_papers.csv
  │     2 dropped (1 lab study confirmed via CrossRef abstract, 1 likely observational)
  └── Dropped 9 low-confidence kw_weak papers (conf < 0.85, all 0.80):
        Methods/econometrics papers, lab studies, and survey-mode papers
        excluded after manual title review
```

---

## 4. Final Numbers

| Category | Count |
|----------|-------|
| Total raw papers collected | 7,178 |
| Registry-confirmed (AEA + J-PAL) | 4,321 |
| LLM-confirmed journal papers | 1,179 |
| **Total verified field experiments** | **5,500** |
| Rejected / unrelated | 1,678 |
| Needs manual review | 0 |

**Year range:** 2015–2026 | **Median year:** 2021
**With abstract:** 5,403 / 5,500 (98.2%)

### Key finding from kw_none pass
Running the LLM on all papers (including those with no keyword match) recovered **164 additional experiments** that the keyword filter missed — papers where experimental language was described differently than the fixed patterns (e.g. "participants were randomly assigned" vs "randomized controlled trial"). Skipping this group would have missed ~14% of LLM-confirmable experiments.

### Verified papers by journal (OpenAlex subset, 1,179 total)
| Journal | Verified | Journal | Verified |
|---------|---------|---------|---------|
| Management Science | 278 | QJE | 56 |
| JDE | 177 | Marketing Science | 49 |
| AER | 128 | RES | 46 |
| JMR | 100 | JPE | 45 |
| Organization Science | 73 | JHE | 40 |
| ISR | 64 | SMJ | 33 |
| MISQ | 63 | Econometrica | 20 |
| RAND | 7 | | |

---

## 5. Output Files

| File | Size | Contents |
|------|------|----------|
| `data_collection/papers/all_papers.csv` | 9.0 MB | All 7,178 records with every label column |
| `data_collection/papers/verified_papers.csv` | 6.5 MB | **5,500 verified experiments** — the main output |
| `data_collection/papers/needs_review.csv` | <1 KB | Empty — all borderline cases resolved in manual review pass |

### Column schema

| Column | Description |
|--------|-------------|
| `paper_id` | Unique ID (prefix: OA\_, AEA\_, JPAL\_) |
| `doi` | Normalized DOI |
| `title` | Paper title |
| `authors_str` | Semicolon-separated author names |
| `year` | Publication / registration year |
| `journal` | Journal abbreviation (OpenAlex papers only) |
| `source` | `openalex` / `aea_registry` / `jpal` |
| `abstract` | Full abstract text (95.5% coverage) |
| `url` | Landing page URL |
| `pdf_url` | Open-access PDF URL (OpenAlex only) |
| `rct_registry_id` | AEA registry ID (e.g. AEARCTR-0001234) |
| `jpal_id` | J-PAL Drupal node ID |
| `citation_count` | Citation count from OpenAlex |
| `date_collected` | ISO date of collection |
| `in_aea` | True if confirmed in AEA registry |
| `in_jpal` | True if confirmed in J-PAL |
| `rct_confirmed` | True if confirmed in either registry |
| `kw_label` | `kw_strong` / `kw_weak` / `kw_none` (OpenAlex only) |
| `has_rct_abstract` | `llm_yes` / `llm_no` / `needs_review` |
| `rct_confidence` | GPT-4o-mini confidence score (0–1) |
| `rct_reason` | One-sentence LLM rationale |

---

## 6. What's Left

### Immediate (no new API keys needed)
- [x] **Manual check of `needs_review` papers** — all 7 borderline cases resolved; 5 confirmed, 2 dropped
- [x] **Spot-check kw_weak classifications** — 9 low-confidence (≤ 0.80) kw_weak papers removed after title review

### Abstract recovery (181 still missing)
- [ ] **Elsevier API with NYU institutional IP** — the Elsevier developer key works but needs to be called from a NYU network connection (VPN or on-campus). This would recover abstracts for ~107 JDE and ~24 JHE papers.

### Dataset expansion
- [ ] **BIT (Behavioural Insights Team)** — their publication listing has no machine-readable API; would need custom scraping of `bi.team/publications`
- [ ] **NBER Working Papers** — many field experiments appear as NBER WPs before journal publication; could add as a 5th source
- [ ] **Re-run OpenAlex in 3–6 months** — abstracts for non-Elsevier missing-abstract papers will appear as OpenAlex indexes them

### Downstream (for the benchmark)
- [ ] **Component extraction** — for each verified paper, extract structured experiment components: intervention design, randomization unit, sample size, outcome measures, key results
- [ ] **Ground-truth annotation** — select ~200 gold-standard papers for human annotation to build the benchmark evaluation set
- [ ] **Multi-agent system integration** — feed `verified_papers.csv` into the survey/experiment design agent as a retrieval corpus
