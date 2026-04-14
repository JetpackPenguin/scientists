# main.py
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Depends, Security
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client, Client
from starlette.status import HTTP_403_FORBIDDEN
from fastapi.responses import FileResponse

# ---------------------------------------------------------------------------
# Environment & Supabase Init
# ---------------------------------------------------------------------------
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")

if not all([SUPABASE_URL, SUPABASE_ANON_KEY, ADMIN_API_KEY]):
    raise RuntimeError("Missing required environment variables in .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# ---------------------------------------------------------------------------
# Security Setup
# ---------------------------------------------------------------------------
# This defines the header name we will look for in requests
API_KEY_NAME = "X-Admin-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def verify_admin_key(api_key: str = Security(api_key_header)):
    """
    Dependency to verify the secret admin key.
    """
    if api_key == ADMIN_API_KEY:
        return api_key
    raise HTTPException(
        status_code=HTTP_403_FORBIDDEN, detail="Could not validate credentials"
    )

# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(title="Musicians API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Pydantic Schemas (Shortened for brevity)
# ---------------------------------------------------------------------------
class MusicianCreate(BaseModel):
    name: str; genre: str; country: str
    bio: str = ""; avatar_url: str = ""

class MusicianUpdate(BaseModel):
    name: Optional[str] = None; genre: Optional[str] = None
    country: Optional[str] = None; bio: Optional[str] = None
    avatar_url: Optional[str] = None

class MusicianResponse(BaseModel):
    id: int; name: str; genre: str; country: str
    bio: str; avatar_url: str; created_at: str

class MusicianListResponse(BaseModel):
    total: int; musicians: list[MusicianResponse]

class MessageResponse(BaseModel):
    message: str

def _row_to_response(row: dict) -> MusicianResponse:
    return MusicianResponse(**row)

# ---------------------------------------------------------------------------
# PUBLIC ROUTES (No Key Required)
# ---------------------------------------------------------------------------
@app.get("/fortytwodugg", response_class=FileResponse)
def read_index():
    # This looks for the index.html file in your GitHub folder
    return "index.html"
    
@app.get("/musicians", response_model=MusicianListResponse)
def list_musicians(genre: Optional[str] = None, search: Optional[str] = None):
    query = supabase.table("musicians").select("*", count="exact")
    if genre: query = query.eq("genre", genre)
    if search: query = query.or_(f"name.ilike.%{search}%,bio.ilike.%{search}%")
    response = query.order("id").execute()
    return MusicianListResponse(total=response.count or 0, musicians=[_row_to_response(r) for r in response.data])

@app.get("/musicians/{musician_id}", response_model=MusicianResponse)
def get_musician(musician_id: int):
    response = supabase.table("musicians").select("*").eq("id", musician_id).execute()
    if not response.data: raise HTTPException(status_code=404, detail="Musician not found")
    return _row_to_response(response.data[0])

# ---------------------------------------------------------------------------
# PROTECTED ROUTES (Requires X-Admin-Key Header)
# ---------------------------------------------------------------------------

@app.post("/musicians", response_model=MusicianResponse, status_code=201, dependencies=[Depends(verify_admin_key)])
def create_musician(payload: MusicianCreate):
    response = supabase.table("musicians").insert(payload.model_dump()).execute()
    if not response.data: raise HTTPException(status_code=400, detail="Failed to create")
    return _row_to_response(response.data[0])

@app.put("/musicians/{musician_id}", response_model=MusicianResponse, dependencies=[Depends(verify_admin_key)])
def replace_musician(musician_id: int, payload: MusicianCreate):
    response = supabase.table("musicians").update(payload.model_dump()).eq("id", musician_id).execute()
    if not response.data: raise HTTPException(status_code=404, detail="Update failed")
    return _row_to_response(response.data[0])

@app.patch("/musicians/{musician_id}", response_model=MusicianResponse, dependencies=[Depends(verify_admin_key)])
def partially_update_musician(musician_id: int, payload: MusicianUpdate):
    update_data = payload.model_dump(exclude_unset=True)
    response = supabase.table("musicians").update(update_data).eq("id", musician_id).execute()
    if not response.data: raise HTTPException(status_code=404, detail="Update failed")
    return _row_to_response(response.data[0])

@app.delete("/musicians/{musician_id}", response_model=MessageResponse, dependencies=[Depends(verify_admin_key)])
def delete_musician(musician_id: int):
    supabase.table("musicians").delete().eq("id", musician_id).execute()
    return MessageResponse(message="Musician deleted successfully")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True)
