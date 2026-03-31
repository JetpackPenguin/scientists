# main.py
"""
Scientists RESTful API Service
===============================
FastAPI + Supabase (Database + Storage)
Methods: GET, POST, PATCH, DELETE
"""

import os
import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client, Client

# ---------------------------------------------------------------------------
# Environment & Supabase Init
# ---------------------------------------------------------------------------
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not all([SUPABASE_URL, SUPABASE_ANON_KEY]):
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY in .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Use service-role client for operations that bypass RLS (optional fallback)
supabase_admin: Client = (
    create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    if SUPABASE_SERVICE_KEY
    else supabase
)

# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Scientists API",
    description="RESTful service for managing scientists with avatar storage",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class ScientistCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, example="Marie Curie")
    field: str = Field(..., min_length=1, max_length=100, example="Physics & Chemistry")
    birth_year: int = Field(..., ge=-3000, le=2030, example=1867)
    nationality: str = Field(..., min_length=1, max_length=100, example="Polish-French")
    bio: str = Field(default="", max_length=2000, example="Pioneer in radioactivity research.")


class ScientistUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    field: Optional[str] = Field(default=None, min_length=1, max_length=100)
    birth_year: Optional[int] = Field(default=None, ge=-3000, le=2030)
    nationality: Optional[str] = Field(default=None, min_length=1, max_length=100)
    bio: Optional[str] = Field(default=None, max_length=2000)


class ScientistResponse(BaseModel):
    id: str
    name: str
    field: str
    birth_year: int
    nationality: str
    bio: str
    avatar_url: str
    created_at: str


class ScientistListResponse(BaseModel):
    total: int
    scientists: list[ScientistResponse]


class MessageResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_public_url(bucket: str, path: str) -> str:
    """Construct the public URL for a file in Supabase Storage."""
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"


def _row_to_response(row: dict) -> ScientistResponse:
    """Convert a database row dict to a ScientistResponse."""
    return ScientistResponse(**row)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

# ── GET /scientists — List all scientists with optional filtering ──────────

@app.get("/scientists", response_model=ScientistListResponse)
def list_scientists(
    field: Optional[str] = Query(default=None, description="Filter by field of study"),
    nationality: Optional[str] = Query(default=None, description="Filter by nationality"),
    search: Optional[str] = Query(default=None, description="Search in name or bio"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """
    Retrieve a paginated list of scientists.
    Supports filtering by field, nationality, and text search.
    """
    query = supabase.table("scientists").select("*", count="exact")

    if field:
        query = query.eq("field", field)
    if nationality:
        query = query.eq("nationality", nationality)
    if search:
        query = query.or_(f"name.ilike.%{search}%,bio.ilike.%{search}%")

    query = query.order("created_at", desc=True).range(offset, offset + limit - 1)

    response = query.execute()

    return ScientistListResponse(
        total=response.count if response.count is not None else 0,
        scientists=[_row_to_response(r) for r in response.data],
    )


# ── GET /scientists/{id} — Get a single scientist by ID ───────────────────

@app.get("/scientists/{scientist_id}", response_model=ScientistResponse)
def get_scientist(scientist_id: str):
    """
    Retrieve a single scientist by their UUID.
    """
    response = (
        supabase.table("scientists")
        .select("*")
        .eq("id", scientist_id)
        .execute()
    )

    if not response.data:
        raise HTTPException(status_code=404, detail="Scientist not found")

    return _row_to_response(response.data[0])


# ── POST /scientists — Create a new scientist (no avatar) ─────────────────

@app.post("/scientists", response_model=ScientistResponse, status_code=201)
def create_scientist(payload: ScientistCreate):
    """
    Create a new scientist record without an avatar.
    Use POST /scientists/upload to create with an avatar.
    """
    response = (
        supabase.table("scientists")
        .insert(payload.model_dump())
        .execute()
    )

    if not response.data:
        raise HTTPException(status_code=400, detail="Failed to create scientist")

    return _row_to_response(response.data[0])


# ── POST /scientists/upload — Create scientist WITH avatar image ───────────

@app.post("/scientists/upload", response_model=ScientistResponse, status_code=201)
async def create_scientist_with_avatar(
    name: str = Form(..., min_length=1),
    field: str = Form(..., min_length=1),
    birth_year: int = Form(...),
    nationality: str = Form(..., min_length=1),
    bio: str = Form(default=""),
    avatar: Optional[UploadFile] = File(default=None),
):
    """
    Create a new scientist with an optional avatar image.
    The avatar is stored in Supabase Storage under the 'avatars' bucket.
    """
    scientist_id = str(uuid.uuid4())
    avatar_url = ""

    # ── Upload avatar if provided ──
    if avatar and avatar.filename:
        # Determine file extension
        ext = avatar.filename.rsplit(".", 1)[-1].lower() if "." in avatar.filename else "png"
        allowed_exts = {"jpg", "jpeg", "png", "gif", "webp", "svg"}
        if ext not in allowed_exts:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type '.{ext}'. Allowed: {', '.join(allowed_exts)}",
            )

        storage_path = f"{scientist_id}.{ext}"
        file_bytes = await avatar.read()

        # Size limit: 5 MB
        if len(file_bytes) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Avatar file must be under 5 MB")

        upload_res = supabase_admin.storage.from_("avatars").upload(
            storage_path,
            file_bytes,
            file_options={"content-type": avatar.content_type or "image/png"},
        )

        avatar_url = _build_public_url("avatars", storage_path)

    # ── Insert scientist record ──
    record = {
        "id": scientist_id,
        "name": name,
        "field": field,
        "birth_year": birth_year,
        "nationality": nationality,
        "bio": bio,
        "avatar_url": avatar_url,
    }

    response = supabase.table("scientists").insert(record).execute()

    if not response.data:
        # Clean up uploaded avatar if DB insert fails
        if avatar_url:
            try:
                storage_path = avatar_url.split("/avatars/")[-1]
                supabase_admin.storage.from_("avatars").remove([storage_path])
            except Exception:
                pass
        raise HTTPException(status_code=400, detail="Failed to create scientist")

    return _row_to_response(response.data[0])


# ── PATCH /scientists/{id} — Update scientist fields ──────────────────────

@app.patch("/scientists/{scientist_id}", response_model=ScientistResponse)
def update_scientist(scientist_id: str, payload: ScientistUpdate):
    """
    Update one or more fields of an existing scientist.
    Fields not provided in the body are left unchanged.
    """
    # Check existence
    check = (
        supabase.table("scientists")
        .select("id")
        .eq("id", scientist_id)
        .execute()
    )
    if not check.data:
        raise HTTPException(status_code=404, detail="Scientist not found")

    # Only send fields that were explicitly set
    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields provided for update")

    response = (
        supabase.table("scientists")
        .update(update_data)
        .eq("id", scientist_id)
        .execute()
    )

    if not response.data:
        raise HTTPException(status_code=400, detail="Failed to update scientist")

    return _row_to_response(response.data[0])


# ── PATCH /scientists/{id}/avatar — Update only the avatar ────────────────

@app.patch("/scientists/{scientist_id}/avatar", response_model=ScientistResponse)
async def update_scientist_avatar(
    scientist_id: str,
    avatar: UploadFile = File(...),
):
    """
    Replace the avatar image for an existing scientist.
    The old avatar file is automatically removed from storage.
    """
    # Check existence & get old avatar URL
    check = (
        supabase.table("scientists")
        .select("id, avatar_url")
        .eq("id", scientist_id)
        .execute()
    )
    if not check.data:
        raise HTTPException(status_code=404, detail="Scientist not found")

    old_avatar_url = check.data[0].get("avatar_url", "")

    # Validate file type
    ext = avatar.filename.rsplit(".", 1)[-1].lower() if "." in avatar.filename else "png"
    allowed_exts = {"jpg", "jpeg", "png", "gif", "webp", "svg"}
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type '.{ext}'. Allowed: {', '.join(allowed_exts)}",
        )

    storage_path = f"{scientist_id}.{ext}"
    file_bytes = await avatar.read()

    if len(file_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Avatar file must be under 5 MB")

    # Upload new avatar
    supabase_admin.storage.from_("avatars").upload(
        storage_path,
        file_bytes,
        file_options={"content-type": avatar.content_type or "image/png"},
        upsert=True,
    )

    new_avatar_url = _build_public_url("avatars", storage_path)

    # Update database
    response = (
        supabase.table("scientists")
        .update({"avatar_url": new_avatar_url})
        .eq("id", scientist_id)
        .execute()
    )

    # Remove old avatar file if it had a different extension
    if old_avatar_url:
        old_path = old_avatar_url.split("/avatars/")[-1]
        if old_path != storage_path:
            try:
                supabase_admin.storage.from_("avatars").remove([old_path])
            except Exception:
                pass

    return _row_to_response(response.data[0])


# ── DELETE /scientists/{id} — Delete a scientist ──────────────────────────

@app.delete("/scientists/{scientist_id}", response_model=MessageResponse)
def delete_scientist(scientist_id: str):
    """
    Delete a scientist and their avatar from storage.
    """
    # Check existence & get avatar URL
    check = (
        supabase.table("scientists")
        .select("id, avatar_url")
        .eq("id", scientist_id)
        .execute()
    )
    if not check.data:
        raise HTTPException(status_code=404, detail="Scientist not found")

    avatar_url = check.data[0].get("avatar_url", "")

    # Delete from database
    response = (
        supabase.table("scientists")
        .delete()
        .eq("id", scientist_id)
        .execute()
    )

    # Remove avatar from storage
    if avatar_url:
        try:
            storage_path = avatar_url.split("/avatars/")[-1]
            supabase_admin.storage.from_("avatars").remove([storage_path])
        except Exception:
            pass  # Log in production, but don't fail the request

    return MessageResponse(message="Scientist deleted successfully")


# ── GET /scientists/fields/distinct — List all distinct fields ─────────────

@app.get("/scientists/fields/distinct", response_model=list[str])
def list_distinct_fields():
    """
    Return a list of all distinct field-of-study values in the database.
    Useful for building filter dropdowns in the frontend.
    """
    response = supabase.table("scientists").select("field").execute()
    fields = sorted(set(row["field"] for row in response.data if row.get("field")))
    return fields


# ── GET /scientists/nationalities/distinct — List all distinct nationalities

@app.get("/scientists/nationalities/distinct", response_model=list[str])
def list_distinct_nationalities():
    """
    Return a list of all distinct nationality values.
    """
    response = supabase.table("scientists").select("nationality").execute()
    nationalities = sorted(
        set(row["nationality"] for row in response.data if row.get("nationality"))
    )
    return nationalities


# ── Health check ───────────────────────────────────────────────────────────

@app.get("/health", response_model=MessageResponse)
def health_check():
    """Simple health check endpoint."""
    return MessageResponse(message="Scientists API is running")


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    import os

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
