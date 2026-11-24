"""FastAPI entry point. Pages and API come in v0.1; see docs/SPEC.md."""
from fastapi import FastAPI

DISCLAIMER = "Risk levels are indicators, not legal advice."

app = FastAPI(title="CopyrightRadar", version="0.0.1")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "disclaimer": DISCLAIMER}
