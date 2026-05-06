from dataclasses import dataclass, field, fields
from typing import Optional


@dataclass
class PaperRecord:
    paper_id:        str
    doi:             Optional[str]
    title:           str
    authors_str:     str
    year:            Optional[int]
    journal:         Optional[str]
    source:          str            # openalex | aea_registry | jpal | bit
    abstract:        Optional[str]
    url:             Optional[str]
    pdf_url:         Optional[str]
    rct_registry_id: Optional[str]
    jpal_id:         Optional[str]
    citation_count:  Optional[int]
    date_collected:  str            # ISO date, e.g. "2026-05-04"


CSV_COLUMNS = [f.name for f in fields(PaperRecord)]
