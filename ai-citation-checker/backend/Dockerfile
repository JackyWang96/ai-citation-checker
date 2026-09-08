FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install -e .
COPY app/ app/
# The APA rules vector index is a committed artifact, not something the
# build generates: no embedding API key is needed at build time, and the
# image is reproducible. Verify it landed rather than discovering it is
# missing when the first retrieval silently returns nothing.
# Only the index ships — the YAML corpus it was built from is a
# maintenance input, and nothing at runtime reads it.
COPY data/apa_rules.db data/apa_rules.db
RUN test -f /app/data/apa_rules.db
RUN mkdir -p /data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
