import os

from fastapi import Depends, FastAPI, HTTPException, Request

RELAY_SECRET = os.environ.get("RELAY_SECRET", "test-secret")

app = FastAPI(title="Claude Tunnel Relay")


async def verify_auth(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {RELAY_SECRET}":
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/participants", dependencies=[Depends(verify_auth)])
async def list_participants():
    return []
