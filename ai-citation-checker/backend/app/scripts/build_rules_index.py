"""Build the APA rules vector index (maintenance command, not a runtime path).

Run this locally or in CI whenever `data/apa_rules/*.yaml` changes, then commit
the regenerated database alongside the corpus:

    python -m app.scripts.build_rules_index
    git add data/apa_rules.db data/apa_rules/

The Docker build only copies the committed artifact — it never runs this
script, so production builds need no embedding API key.

Requires OPENAI_API_KEY (embedding only; unrelated to ANTHROPIC_API_KEY).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.services.vectors import open_rules_db, pack_vector, vec_version

REQUIRED_FIELDS = ("chunk_id", "category", "title", "text", "source_url")
DEFAULT_CORPUS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "apa_rules"
DEFAULT_OUT = DEFAULT_CORPUS_DIR.parent / "apa_rules.db"


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


def embed_text(chunk: dict[str, Any]) -> str:
    """What actually gets embedded.

    Title and category are included so a query naming a citation *type*
    ("encyclopedia entry", "edited book chapter") can match the right chunk
    even when it shares no distinctive vocabulary with the body text.
    """
    return f"{chunk['category']} | {chunk['title']}\n{chunk['text']}".strip()


def embed_all(texts: list[str], model: str, batch_size: int = 64) -> list[list[float]]:
    from openai import OpenAI

    client = OpenAI()  # reads OPENAI_API_KEY
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        resp = client.embeddings.create(model=model, input=batch)
        # The API preserves input order, but sort by index rather than trust it.
        vectors.extend(item.embedding for item in sorted(resp.data, key=lambda d: d.index))
        print(f"  embedded {min(start + batch_size, len(texts))}/{len(texts)}", flush=True)
    return vectors


def is_unit_length(vec: list[float], tol: float = 1e-3) -> bool:
    return abs(sum(x * x for x in vec) ** 0.5 - 1.0) < tol


def write_index(
    out_path: Path,
    chunks: list[dict[str, Any]],
    vectors: list[list[float]],
    meta: dict[str, str],
) -> None:
    if out_path.exists():
        out_path.unlink()

    db = open_rules_db(str(out_path))

    dim = len(vectors[0])
    db.executescript(
        f"""
        CREATE TABLE rule_chunks (
            rowid         INTEGER PRIMARY KEY,
            chunk_id      TEXT UNIQUE NOT NULL,
            category      TEXT NOT NULL,
            title         TEXT NOT NULL,
            text          TEXT NOT NULL,
            source_url    TEXT NOT NULL,
            related_rules TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE rule_vectors USING vec0(embedding float[{dim}]);
        CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    for rowid, (chunk, vec) in enumerate(zip(chunks, vectors), start=1):
        db.execute(
            "INSERT INTO rule_chunks "
            "(rowid, chunk_id, category, title, text, source_url, related_rules) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                rowid,
                chunk["chunk_id"],
                chunk["category"],
                chunk["title"],
                chunk["text"].strip(),
                chunk["source_url"],
                json.dumps(chunk["related_rules"]),
            ),
        )
        db.execute(
            "INSERT INTO rule_vectors(rowid, embedding) VALUES (?,?)",
            (rowid, pack_vector(vec)),
        )
    db.executemany(
        "INSERT INTO index_meta(key, value) VALUES (?,?)", sorted(meta.items())
    )
    db.commit()
    db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--model", default="text-embedding-3-small")
    args = parser.parse_args(argv)

    chunks = load_corpus(args.corpus)
    digest = corpus_sha256(chunks)
    print(f"corpus: {len(chunks)} chunks from {args.corpus} (sha256 {digest[:12]}…)")

    vectors = embed_all([embed_text(c) for c in chunks], args.model)
    dim = len(vectors[0])
    if any(len(v) != dim for v in vectors):
        raise RuntimeError("embedding API returned inconsistent dimensions")

    # Record what the API actually returned rather than assuming it normalises.
    normalized = all(is_unit_length(v) for v in vectors)

    meta = {
        "embedding_provider": "openai",
        "embedding_model": args.model,
        "embedding_dim": str(dim),
        "normalized": str(normalized).lower(),
        "corpus_sha256": digest,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sqlite_vec_version": vec_version(),
    }
    write_index(args.out, chunks, vectors, meta)

    size_mb = args.out.stat().st_size / 1_048_576
    print(f"wrote {args.out} ({size_mb:.1f} MB) dim={dim} normalized={normalized}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
