"""Query-side embedding for the APA rules index.

The only rule that matters here: this must produce vectors in the *same space*
as the ones `build_rules_index` wrote. Different provider or different model
means the vectors are not comparable — the KNN query will still "work" and
return confidently ranked nonsense. `rules_retriever.verify_rules_index`
enforces the match; this module just has to stay consistent with config.

Async because it is called from FastAPI request handling — a blocking HTTP
call here would stall the event loop for every other in-flight request.
"""
from __future__ import annotations

from typing import Optional

import openai

import app.config as cfg


async def embed_query(
    text: str,
    client: Optional[openai.AsyncOpenAI] = None,
) -> list[float]:
    """Embed one query string. `client` is injectable for tests."""
    owns_client = client is None
    if owns_client:
        client = openai.AsyncOpenAI(api_key=cfg.OPENAI_API_KEY)
    try:
        resp = await client.embeddings.create(model=cfg.EMBEDDING_MODEL, input=[text])
    finally:
        if owns_client:
            await client.close()

    vec = resp.data[0].embedding
    if len(vec) != cfg.EMBEDDING_DIM:
        raise RuntimeError(
            f"embedding model {cfg.EMBEDDING_MODEL} returned dim={len(vec)}, "
            f"but the index was built with dim={cfg.EMBEDDING_DIM}"
        )
    return vec
