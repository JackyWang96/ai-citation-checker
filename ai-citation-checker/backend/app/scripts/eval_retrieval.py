"""Recall evaluation for the APA rules retriever (manual command, not CI).

Run this whenever the corpus, the embedding model, or `build_query` changes:

    OPENAI_API_KEY=... python -m app.scripts.eval_retrieval

It makes real embedding calls (12 of them, well under a cent), which is why
it is a script rather than a test — CI stays offline and deterministic.

The acceptance gate from the design doc is **top-3 >= 6/6**. top-1 is
reported for information only: all top-k chunks are fed to the model, so
top-3 is the metric that actually predicts suggestion quality.

`--naive` reruns the same samples with the whole reference text as the query,
reproducing the PR 1 finding that proper nouns wreck recall. Keep it: it is
the evidence for why `build_query` looks the way it does.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import app.config as cfg
from app.services.embeddings import embed_query
from app.services.rules_retriever import build_query, open_index, search_vector

# Real citations that produced real bugs, each paired with the chunk a human
# would reach for. Expected sets allow more than one defensible target.
SAMPLES: list[tuple[str, str, list[str], set[str]]] = [
    (
        "Leuckert 2024 — journal name run into the title, no comma before volume",
        "Leuckert, S. (2024). Stop Focusing on What the Dictionary Says! "
        "Meta-Perspectives on Lexicographical Resources of Mountaineering "
        "English on Reddit. Dictionaries Journal of the Dictionary Society "
        "of North America 45, 373–401.",
        ["Journal name should be in italics (APA 7th R003)"],
        {"apa7-journal-volume-issue-pages", "apa7-journal-italics-scope",
         "apa7-journal-basic-shape"},
    ),
    (
        "Schmitt/Wolter — two references merged into one entry",
        "Schmitt, N., Sonbul, S., Vilkaitė-Lozdienė, L., & Macis, M. (2019). "
        "Formulaic language and collocation. In C. A. Chapelle (Ed.), The "
        "encyclopedia of applied linguistics. John Wiley & Sons. "
        "https://doi.org/10.1002/9781405198431.wbeal0433.pub2 "
        "Wolter, B., & Gyllstad, H. (2013). Frequency of input and L2 "
        "collocational processing. Studies in Second Language Acquisition, "
        "35(3), 451-482.",
        ["Two references appear to be merged into one entry"],
        {"apa7-merged-references"},
    ),
    (
        "MacWhinney/Ellis — chapter editors written in author order",
        "MacWhinney, B. (2008). A unified model. In Robinson, P., & Ellis, "
        "N. C. (Eds.), Handbook of cognitive linguistics (pp. 351-381). Routledge.",
        ["R016: Editors use 'F. M. Last' order (initials first), not 'Last, F. M.'."],
        {"apa7-chapter-editor-name-order", "apa7-edited-book-chapter"},
    ),
    (
        "Wang & Sun 2020 — article number instead of a page range",
        "Wang, C., & Sun, T. (2020). Relationship between self-efficacy and "
        "language proficiency: A meta-analysis. System, 95, 102366.",
        ["Journal article may be missing volume/issue/page numbers (APA 7th R020)"],
        {"apa7-journal-article-number", "apa7-journal-missing-metadata"},
    ),
    (
        "Meichenbaum 1977 — whole book",
        "Meichenbaum, D. (1977). Cognitive-behavior modification: An "
        "integrative approach. Plenum Press.",
        ["Title does not match authoritative record"],
        {"apa7-whole-book", "apa7-title-case-vs-sentence-case",
         "apa7-italics-by-type"},
    ),
    (
        "Van Vu 2022 — multi-word surname read as a name-order error",
        "Van Vu, D., & Peters, E. (2022). Incidental learning of collocations "
        "from meaningful input. Studies in Second Language Acquisition, "
        "44(3), 685-707. https://doi.org/10.1017/S0272263121000462",
        ["Author name mismatch"],
        {"apa7-author-name-format"},
    ),
]


async def _run(naive: bool) -> int:
    db = open_index()
    top1 = top3 = 0
    for label, raw_text, reasons, expected in SAMPLES:
        citation = {
            "raw_text": raw_text,
            "issues": [{"type": "format_violation", "reason": r} for r in reasons],
        }
        query = raw_text + " " + ". ".join(reasons) if naive else build_query(citation)
        hits = search_vector(db, await embed_query(query), 3)
        ids = [h.chunk_id for h in hits]
        hit1 = ids[0] in expected if ids else False
        hit3 = bool(expected & set(ids))
        top1 += hit1
        top3 += hit3
        print(f"{'PASS' if hit3 else 'FAIL':4}  {label}")
        print(f"      query: {query[:110]}...")
        for rank, h in enumerate(hits, 1):
            mark = "*" if h.chunk_id in expected else " "
            print(f"      {mark}{rank}. {h.chunk_id:42} d={h.distance:.3f}")
        print()

    n = len(SAMPLES)
    print(f"{'naive' if naive else 'structured'} query — top-1 {top1}/{n}, top-3 {top3}/{n}")
    if not naive and top3 < n:
        print("FAILS the design gate (top-3 must be 6/6): fix build_query or the "
              "corpus, do not raise k to paper over it.", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--naive", action="store_true",
                        help="use the full reference text as the query (the bad baseline)")
    args = parser.parse_args(argv)
    if not cfg.RAG_ENABLED:
        print("OPENAI_API_KEY is not set", file=sys.stderr)
        return 2
    return asyncio.run(_run(args.naive))


if __name__ == "__main__":
    sys.exit(main())
