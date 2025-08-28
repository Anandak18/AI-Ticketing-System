from fastapi import APIRouter, HTTPException
from ..models.schemas import ChatRequest, ChatResponse
from ..services.ticket_engine import load_json, save_json
from ..services.slot_extractor import extract_with_openai
from pathlib import Path
from datetime import datetime
import json
import os
from openai import AzureOpenAI
from typing import Dict, List, Optional
from ..services.comment_validator import is_valid_comment
from ..config import TICKETS_PATH,MEMORY_PATH,CONFIDENCE_CLOSE_THRESHOLD

router = APIRouter()
# ------------------------------
# Azure OpenAI client
# ------------------------------
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version="2024-02-15-preview",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)

# ------------------------------
# Azure LLM Intent Detection
# ------------------------------
def llm_intent(message: str) -> str:
    prompt = f"""
    You are a ticket management assistant.
    Classify the user message into one of these categories:
    1. create -> when the user wants to create a new ticket
    2. view -> when the user wants to see, check, or ask about existing tickets
    3. update -> when the user wants to modify, edit, approve, or reject an existing ticket

    Only respond with one word: create, view, or update.
    User message: "{message}"
    """
    resp = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    return resp.choices[0].message.content.strip().lower()

# ------------------------------
# Chat endpoint
# ------------------------------
@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        tickets = load_json(TICKETS_PATH)
        memory = load_json(MEMORY_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load data: {str(e)}")

    intent = llm_intent(req.message)
    response_message = ""

    # ------------------- CREATE TICKET -------------------
    if intent == "create":
        if ":" in req.message:
            desc = req.message.split(":", 1)[1].strip().strip('"').strip("'")
            if not desc:
                response_message = "Please provide a description for the ticket."
            else:
                existing_nums = [
                    int(t["ticket_no"].split("-")[1])
                    for t in tickets
                    if t.get("ticket_no", "").startswith("TICKET-")
                ]
                next_num = max(existing_nums) + 1 if existing_nums else 1
                new_id = f"TICKET-{next_num:04d}"

                # ✅ Extract slots immediately
                slots = extract_with_openai(desc)

                # ✅ Decide status based on confidence
                if slots["aggregate_confidence"] < CONFIDENCE_CLOSE_THRESHOLD:
                    status = "needs-review"
                else:
                    status = "closed"

                new_ticket = {
                    "ticket_no": new_id,
                    "description": desc,
                    "status": status,
                    "metadata": {
                        "createdAt": datetime.utcnow().isoformat() + "Z",
                        "createdBy": "chat-user"
                    },
                    "slots": slots
                }

                tickets.append(new_ticket)
                save_json(TICKETS_PATH, tickets)

                response_message = (
                    f"✅ Ticket {new_id} created!\n"
                    f"Description: {desc}\n"
                    f"Status: {status}\n"
                    f"(slots extracted at creation, confidence={slots['aggregate_confidence']:.2f})"
                )
        else:
            response_message = "Please use the format: 'New ticket: description'"

    # ------------------- VIEW TICKETS -------------------
    elif intent == "view":
        system_prompt = f"""
        You are a ticket assistant. Answer user questions strictly based on the ticket data and chat memory provided.
        Do not invent or assume any ticket IDs, statuses, or details. Only use the information given.

        Tickets data (JSON): {json.dumps(tickets)}
        Chat memory (JSON): {json.dumps(memory)}

        Answer the following user question exactly and concisely:
        User message: "{req.message}"
        """
        resp = client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            messages=[{"role": "system", "content": system_prompt}],
            temperature=0
        )
        response_message = resp.choices[0].message.content.strip()

    # ------------------- REVIEW TICKET -------------------
    elif intent in ("review", "update"):
        system_prompt = f"""
        Extract ticket number and review action (APPROVE, REJECT, EDIT) from the message.
        Respond in JSON format:
        {{
        "ticket_no": "TICKET-0001",
        "action": "APPROVE",
        "comment": "User comment for review"
        }}
        Message: "{req.message}"
        """
        resp = client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            messages=[{"role": "user", "content": system_prompt}],
            temperature=0
        )
        content = resp.choices[0].message.content.strip()

        # Cleanup possible ```json wrappers
        if content.startswith("```json"):
            content = content[len("```json"):].strip()
        if content.startswith("```"):
            content = content[3:].strip()
        if content.endswith("```"):
            content = content[:-3].strip()

        try:
            review_data = json.loads(content)
            ticket_no = review_data.get("ticket_no")
            action = review_data.get("action", "").upper()
            comments = review_data.get("comment", "")
        except Exception as e:
            print(e)
            return {
                "message": """Please use the format:
                {
                "ticketNo": "string",
                "action": "APPROVE | REJECT | EDIT",
                "comments": "string (min 15 words, should include what changed and at least one actionable step)"
                }""",
                "valid": False
            }

        # Validate ticket exists
        ticket = next((t for t in tickets if t.get("ticket_no") == ticket_no), None)
        if not ticket:
            return {"message": f"Ticket {ticket_no} not found.", "valid": False}

        # Validate action
        if action not in ("APPROVE", "REJECT", "EDIT"):
            return {"message": f"Invalid action '{action}'. Use APPROVE, REJECT, or EDIT.", "valid": False}

        # Validate comment
        validation = is_valid_comment(comments)
        if not validation.get("valid"):
            return {"message": f"Comments are invalid: {validation.get('message')}", "valid": False}

        # Build review entry for memory.json
        entry = {
            "ticketId": ticket_no,
            "summary": comments.split('.')[0].strip(),
            "resolution_steps": comments.strip(),
            "user": "chat-user",
            "timestamp": datetime.utcnow().isoformat() + 'Z',
            "action": action
        }
        memory.append(entry)
        save_json(MEMORY_PATH, memory)

        # ✅ Update ticket in place
        ticket["status"] = {
            "APPROVE": "APPROVED",
            "REJECT": "REJECTED",
            "EDIT": "EDITED"
        }[action]

        ticket.setdefault("metadata", {})["lastReviewAction"] = action
        ticket["metadata"]["updatedAt"] = datetime.utcnow().isoformat() + 'Z'

        # ✅ Store resolution inside ticket
        ticket["review_summary"] = comments.split('.')[0].strip()
        ticket["resolution_steps"] = comments.strip()

        # Replace the ticket in tickets list instead of appending
        for i, t in enumerate(tickets):
            if t.get("ticket_no") == ticket_no:
                tickets[i] = ticket
                break

        save_json(TICKETS_PATH, tickets)

        response_message = (
            f"✅ Ticket {ticket_no} reviewed successfully.\n"
            f"Status: {ticket['status']}\n"
            f"Summary: {ticket['review_summary']}"
        )

    else:
        response_message = "I can help you create, view, or review tickets. Example:\n• 'New ticket: login page error'\n• 'Show open tickets'\n• 'Approve ticket TICKET-0001 with comment...'"

    # ------------------- SAVE CHAT -------------------
    memory_entry = {
        "user_message": req.message,
        "bot_response": response_message,
        "timestamp": datetime.utcnow().isoformat() + 'Z'
    }
    memory.append(memory_entry)
    save_json(MEMORY_PATH,memory)

    return {"message": response_message, "valid": True}

