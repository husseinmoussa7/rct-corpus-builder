import os
import pathlib

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT  = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR      = PROJECT_ROOT / "data_collection"
RAW_DIR       = DATA_DIR / "papers" / "raw"
CSV_PATH      = DATA_DIR / "papers" / "all_papers.csv"
LOG_PATH      = DATA_DIR / "collection_log.json"
MANIFEST_PATH = DATA_DIR / "papers" / ".processed_manifest.txt"

# ── OpenAlex ───────────────────────────────────────────────────────────────
# Set OPENALEX_EMAIL in your .env for polite-pool access (faster rate limits).
# See: https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication
OPENALEX_BASE       = "https://api.openalex.org/works"
OPENALEX_EMAIL      = os.getenv("OPENALEX_EMAIL", "your-email@example.com")
OPENALEX_PER_PAGE   = 200
OPENALEX_SLEEP      = 0.12   # ~8 req/sec, under the 10 req/sec polite-pool limit

# Search terms sent to OpenAlex's `search` parameter (server-side text search
# across title + abstract). Multiple terms are run as separate queries and
# deduplicated by OpenAlex work ID, so there is no double-counting.
FIELD_EXPERIMENT_SEARCHES = [
    "field experiment",
    "randomized controlled trial",
    "randomized evaluation",
    "natural field experiment",
]

# ── Journal ISSN map ───────────────────────────────────────────────────────
JOURNAL_ISSNS: dict[str, str] = {
    "AER":     "0002-8282",
    "QJE":     "0033-5533",
    "JPE":     "0022-3808",
    "RAND":    "0741-6261",
    "RES":     "0034-6527",
    "ECMA":    "0012-9682",
    "JDE":     "0304-3878",
    "JHE":     "0167-6296",
    "MKTSCI":  "0732-2399",
    "MGTSCI":  "0025-1909",
    "JMR":     "0022-2437",
    "ISR":     "1047-7047",
    "MISQ":    "0276-7783",
    "SMJ":     "0143-2095",
    "ORGSCI":  "1047-7039",
}

# ── AEA RCT Registry ───────────────────────────────────────────────────────
AEA_BASE_URL   = "https://www.socialscienceregistry.org/trials"
AEA_WAIT_SEC   = 10
AEA_PAGE_SLEEP = 2.0

# ── J-PAL ─────────────────────────────────────────────────────────────────
JPAL_BASE_URL   = "https://www.povertyactionlab.org/evaluations"
JPAL_WAIT_SEC   = 10
JPAL_PAGE_SLEEP = 2.0

# ── BIT ────────────────────────────────────────────────────────────────────
BIT_BASE_URL   = "https://www.bi.team/publications/"
BIT_PAGE_SLEEP = 1.5
