from fastapi import APIRouter, HTTPException
from typing import List
import json, datetime
from ..models.schemas import Ticket, ReviewActionRequest,TicketSlots,SlotConfidence
from ..services.ticket_engine import load_json, save_json
from ..services.comment_validator import is_valid_comment
from ..config import TICKETS_PATH, MEMORY_PATH

router = APIRouter()

@router.get("/tickets", response_model=List[Ticket], response_model_by_alias=True)
def list_tickets(status: str = None, severity: str = None):
    tickets = load_json(TICKETS_PATH)
 
    if status:
        tickets = [t for t in tickets if t.get("status") == status]
    if severity:
        tickets = [
    t for t in tickets
    if (t.get("slots") or {}).get("severity", "").lower() == severity.lower()
]
    return tickets

@router.post("/review", response_model=Ticket, response_model_by_alias=True)
def review_action(req: ReviewActionRequest):
    tickets = load_json(TICKETS_PATH)
    
    # --- Find the ticket ---
    found = next((t for t in tickets if t.get("ticket_no") == req.ticket_no), None)
    if not found:
        raise HTTPException(status_code=404, detail={"message": "ticket not found"})
    
    # --- Validate action and comments ---
    if req.action not in ("APPROVE", "EDIT", "REJECT"):
        raise HTTPException(status_code=422, detail={"message": "invalid action"})
    if not is_valid_comment(req.comments):
        raise HTTPException(
            status_code=400,
            detail={"message": "comments are not valid (min 15 words, include what changed and at least one actionable step, and no placeholders)"}
        )
    
    # --- Append to memory.json ---

    memory = []
    if MEMORY_PATH.exists():
        content = MEMORY_PATH.read_text(encoding="utf-8").strip()
        if content:
            memory = json.loads(content)
    entry = {
        "ticketId": req.ticket_no,
        "summary": req.comments.split('.')[0].strip(),
        "resolution_steps": req.comments.strip(),
        "user": "reviewer@example.com",
        "timestamp": datetime.datetime.utcnow().isoformat() + 'Z'
    }
    memory.append(entry)
    MEMORY_PATH.write_text(json.dumps(memory, indent=2), encoding='utf-8')
    
    # --- Update ticket ---
    found.setdefault("metadata", {})["lastReviewAction"] = req.action
    found["status"] = {
        "APPROVE": "APPROVED",
        "EDIT": "EDITED",
        "REJECT": "REJECTED",
    }[req.action]
    save_json(TICKETS_PATH, tickets)
    
    # --- Convert slots to TicketSlots format ---
    if found.get("slots"):
        slots = found["slots"]
        # adjust import path if needed
        found["slots"] = TicketSlots(
            issueType=SlotConfidence(value=slots.get("issue_type", ""), confidence=1.0),
            severity=SlotConfidence(value=slots.get("severity", ""), confidence=1.0),
            affectedSystem=SlotConfidence(value=slots.get("affected_system", ""), confidence=1.0)
        )
    
    # --- Return ticket ---
    return found