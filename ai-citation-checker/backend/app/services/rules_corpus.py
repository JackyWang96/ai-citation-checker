"""Loading and fingerprinting the APA rules corpus.

Shared by the build script (which embeds the corpus) and the retriever (which
re-hashes it at runtime to prove the committed index is still in sync). Both
sides MUST use the same hash function — if they diverged, the fingerprint
check would pass while the index was actually stale, which is the exact
failure the check exists to catch.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

REQUIRED_FIELDS = ("chunk_id", "category", "title", "text", "source_url")


def load_corpus(corpus_dir: Path) -> list[dict[str, Any]]:
    """Load and validate every chunk, in a deterministic order.

    Order matters: the corpus hash must be reproducible across machines, so
    files are read sorted and chunks are sorted by chunk_id.
    """
    chunks: list[dict[str, Any]] = []
    for path in sorted(corpus_dir.glob("*.yaml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if not isinstance(loaded, list):
            raise ValueError(f"{path.name}: expected a list of chunks")
        for entry in loaded:
            missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
            if missing:
                raise ValueError(
                    f"{path.name}: chunk {entry.get('chunk_id', '<no id>')!r} "
                    f"is missing required field(s): {', '.join(missing)}"
                )
            entry.setdefault("related_rules", [])
            entry["_source_file"] = path.name
            chunks.append(entry)

    ids = [c["chunk_id"] for c in chunks]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate chunk_id(s): {', '.join(duplicates)}")
    if not chunks:
        raise ValueError(f"no chunks found in {corpus_dir}")

    return sorted(chunks, key=lambda c: c["chunk_id"])


def corpus_sha256(chunks: list[dict[str, Any]]) -> str:
    """Hash the semantic content only, so cosmetic YAML edits don't force a
    rebuild but any change to embedded text does."""
    payload = [
        {f: c[f] for f in REQUIRED_FIELDS} | {"related_rules": c["related_rules"]}
        for c in chunks
    ]
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
