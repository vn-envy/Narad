"""
Document review API: the crop-confirmation screen behind /?review=<id>.

GET  /documents/reviews?status=pending         → the caller's reviews, newest first
GET  /documents/reviews/{id}                   → one review (values, pages, offer)
GET  /documents/reviews/{id}/items/{item}/crop → JPEG of the region a value was read from
GET  /documents/reviews/{id}/pages/{n}         → JPEG of one page
POST /documents/reviews/{id}/save              → write only the confirmed values
POST /documents/reviews/{id}/escalate          → a clearer read of hard pages (explicit consent)
POST /documents/reviews/{id}/discard           → delete a pending review and its images

Every route resolves the review inside the caller's own profile folder (the
auth middleware sets the profile scope), so another profile's review id is a
plain 404, like /media.
"""

from __future__ import annotations

import asyncio
from typing import Any

import document_review
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

document_review_router = APIRouter()

_IMAGE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
}


class ItemDecision(BaseModel):
    id: str
    action: str = "drop"  # confirm | drop
    label: str | None = None
    value: str | None = None
    unit: str | None = None
    reference_range: str | None = None
    details: dict[str, Any] | None = None


class SaveRequest(BaseModel):
    items: list[ItemDecision] = Field(default_factory=list)
    document: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, bool] = Field(default_factory=dict)


class EscalateRequest(BaseModel):
    pages: list[int] = Field(default_factory=list)
    consent: bool = False


def _review_or_404(review_id: str) -> dict[str, Any]:
    review = document_review.load_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Not Found")
    return review


@document_review_router.get("/documents/reviews")
async def list_document_reviews(status: str = "", limit: int = 20):
    reviews = await asyncio.to_thread(document_review.list_reviews, status, limit)
    return {"reviews": reviews}


@document_review_router.get("/documents/reviews/{review_id}")
async def get_document_review(review_id: str):
    review = await asyncio.to_thread(_review_or_404, review_id)
    return document_review.public_review(review)


@document_review_router.get("/documents/reviews/{review_id}/items/{item_id}/crop")
async def document_review_crop(review_id: str, item_id: str):
    data = await asyncio.to_thread(document_review.render_crop, review_id, item_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return Response(content=data, media_type="image/jpeg", headers=_IMAGE_HEADERS)


@document_review_router.get("/documents/reviews/{review_id}/pages/{page}")
async def document_review_page(review_id: str, page: int):
    data = await asyncio.to_thread(document_review.render_page, review_id, page)
    if data is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return Response(content=data, media_type="image/jpeg", headers=_IMAGE_HEADERS)


@document_review_router.post("/documents/reviews/{review_id}/save")
async def save_document_review(review_id: str, req: SaveRequest):
    result = await asyncio.to_thread(
        document_review.save_review,
        review_id,
        [item.model_dump(exclude_none=True) for item in req.items],
        document=req.document,
        options=req.options,
    )
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Not Found")
    if result.get("status") == "error":
        raise HTTPException(status_code=409, detail=result.get("message"))
    return result


@document_review_router.post("/documents/reviews/{review_id}/escalate")
async def escalate_document_review(review_id: str, req: EscalateRequest):
    from document_fields import escalate

    result = await asyncio.to_thread(escalate, review_id, req.pages, consent=req.consent)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Not Found")
    if result.get("status") == "needs_consent":
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@document_review_router.post("/documents/reviews/{review_id}/discard")
async def discard_document_review(review_id: str):
    if not await asyncio.to_thread(document_review.discard_review, review_id):
        raise HTTPException(status_code=404, detail="Not Found")
    return {"status": "discarded", "review_id": review_id}
