from __future__ import annotations

"""
Pydantic schemas for the ticket service API.

These models define request/response payloads used by FastAPI endpoints.
"""

from pydantic import BaseModel, Field


class TicketCreateRequest(BaseModel):
    """Request model for POST /tickets."""

    ticket_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    submitter: str | None = None
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)


class TicketResponse(BaseModel):
    """Response model for ticket resources."""

    ticket_id: str
    created_at: str
    channel: str
    submitter: str | None
    title: str
    description: str
    topic: str | None
    topic_confidence: float | None
    risk_level: str | None
    route_action: str | None
    status: str
    receipt: str | None
    updated_at: str


class MetricsResponse(BaseModel):
    """Aggregated metrics response model for GET /metrics."""

    total_tickets: int
    status_counts: dict[str, int]
    route_counts: dict[str, int]
    degrade_count: int
