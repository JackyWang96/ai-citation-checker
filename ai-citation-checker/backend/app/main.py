from fastapi import FastAPI

app = FastAPI(title="AI Citation Checker")


@app.get("/healthz")
async def health():
    return {"status": "ok"}
