from typing import Optional, Dict, Any, Union
from pydantic import BaseModel, Field, validator

# Single class for slot confidence
class SlotConfidence(BaseModel):
    value: Optional[str] = None
    confidence: Optional[float] = None

    class Config:
        allow_population_by_field_name = True

# TicketSlots with fields that can be either string or SlotConfidence
class TicketSlots(BaseModel):
    issue_type: Optional[Union[str, SlotConfidence]] = None
    severity: Optional[Union[str, SlotConfidence]] = None
    affected_system: Optional[Union[str, SlotConfidence]] = None

    class Config:
        allow_population_by_field_name = True

# Main Ticket model
class Ticket(BaseModel):
    ticket_no: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    slots: Optional[TicketSlots] = None
    aggregate_confidence: Optional[float] = None
    proposedFix: Optional[str] = None
    metadata: Dict[str, Any] = {}

    class Config:
        allow_population_by_field_name = True

# Chat models
class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    message: str
    updatedTicket: Optional[Ticket] = None

# Review action request with validations
class ReviewActionRequest(BaseModel):
    ticket_no: str
    action: str  # APPROVE | EDIT | REJECT
    comments: str

    @validator("action")
    def validate_action(cls, v):
        allowed = {"APPROVE", "EDIT", "REJECT"}
        if v not in allowed:
            raise ValueError(f"Invalid action. Must be one of {allowed}")
        return v

    @validator("comments")
    def validate_comments(cls, v):
        if not v or len(v.split()) < 15:
            raise ValueError("Comments must be at least 15 words long")
        return v

# New ticket request
class NewTicketRequest(BaseModel):
    description: str
