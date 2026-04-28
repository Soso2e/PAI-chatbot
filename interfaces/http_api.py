import os
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from core import chat_controller

app = FastAPI(title="PAI-Chatbot HTTP API")

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _verify_key(key: str = Security(_api_key_header)):
    expected = os.getenv("HTTP_API_KEY", "")
    if not expected:
        return  # 認証未設定なら全許可（開発用）
    if key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ── Schemas ──────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    user_id: str = "anonymous"
    session_id: str
    db_name: str = "general"


class ChatResponse(BaseModel):
    reply: str
    db_used: str
    session_id: str


class SwitchRequest(BaseModel):
    session_id: str
    db_name: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(_verify_key)])
async def chat(req: ChatRequest):
    if req.db_name not in chat_controller.available_dbs():
        raise HTTPException(status_code=400, detail=f"DB '{req.db_name}' not found")
    try:
        reply = await chat_controller.process(req.message, req.session_id, req.db_name)
    except RuntimeError as exc:
        detail = str(exc)
        status_code = 504 if "timed out" in detail.lower() else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return ChatResponse(reply=reply, db_used=req.db_name, session_id=req.session_id)


@app.get("/db/list", dependencies=[Depends(_verify_key)])
async def db_list():
    return {"databases": chat_controller.available_dbs()}


@app.post("/db/switch", dependencies=[Depends(_verify_key)])
async def db_switch(req: SwitchRequest):
    if req.db_name not in chat_controller.available_dbs():
        raise HTTPException(status_code=400, detail=f"DB '{req.db_name}' not found")
    return {"session_id": req.session_id, "db_name": req.db_name}


@app.get("/status", dependencies=[Depends(_verify_key)])
async def status():
    import json
    from pathlib import Path
    llm_path = Path(__file__).parent.parent / "config" / "llm.json"
    with open(llm_path, encoding="utf-8") as f:
        llm = json.load(f)
    return {
        "llm_provider": llm["provider"],
        "model": llm["model"],
        "base_url": llm["base_url"],
        "available_dbs": chat_controller.available_dbs(),
    }
