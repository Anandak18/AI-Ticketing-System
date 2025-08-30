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
import matplotlib.pyplot as plt
from io import BytesIO
import base64
import pandas as pd
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
Classify the user message into one of these categories:
1. create -> when the user wants to create a new ticket
2. view -> when the user wants to see, check, or ask about existing tickets, 
           including counts, lists, summaries, or statistics.
3. update -> when the user wants to modify a ticket
4. review -> when the user wants to approve/reject/edit a ticket
5. delete -> when the user asks to delete a ticket
6. graph -> when the user explicitly asks for a chart, graph, plot, visualization, diagram, or trend.

Always return only one word: create, view, update, review, delete, or graph.

Message: "{message}"
"""
    resp = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    print("INTENT",resp.choices[0].message.content.strip().lower())
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

                # Extract slots immediately
                slots = extract_with_openai(desc)

                # Decide status based on confidence
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
                    f"Ticket {new_id} created!\n"
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
        last_context = memory[-1] if memory else None
        if last_context:
            system_prompt += f"\nPrevious conversation: {json.dumps(last_context)}"

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
            return {"message": f"{validation.get('message')}", "valid": False}

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

        # Update ticket in place
        ticket["status"] = {
            "APPROVE": "APPROVED",
            "REJECT": "REJECTED",
            "EDIT": "EDITED"
        }[action]

        ticket.setdefault("metadata", {})["lastReviewAction"] = action
        ticket["metadata"]["updatedAt"] = datetime.utcnow().isoformat() + 'Z'

        # Store resolution inside ticket
        ticket["review_summary"] = comments.split('.')[0].strip()
        ticket["resolution_steps"] = comments.strip()

        # Replace the ticket in tickets list instead of appending
        for i, t in enumerate(tickets):
            if t.get("ticket_no") == ticket_no:
                tickets[i] = ticket
                break

        save_json(TICKETS_PATH, tickets)

        response_message = (
            f"Ticket {ticket_no} reviewed successfully.\n"
            f"Status: {ticket['status']}\n"
            f"Summary: {ticket['review_summary']}"
        )
    elif intent == "delete":
        response_message = "You can view and edit the ticket but the deletion is not allowed."


    elif intent == "graph":
  

        
        try:
            # ------------------ Prepare LLM prompt ------------------
            system_prompt = f"""
            You are a data visualization assistant.

            Context:
            - You are given the complete tickets dataset in JSON below.
            - You are also given a user query that asks for a chart.
            - Your job is to interpret the query, decide what fields from the tickets JSON are relevant, 
            and return a chart specification in JSON format.

            Rules:
            - Do NOT invent data.
            - Use ONLY fields that exist in the tickets JSON.
            - Always base the chart on the user's query.
            - Always return ONLY a valid JSON object.
            - Do NOT include explanations, markdown, or text outside JSON.
            - Follow this schema exactly:

            {{
            "chart_type": "bar | line | pie | histogram",
            "x": "field_name_for_x_axis",
            "y": "field_name_for_y_axis_or_counts",
            "aggregation": "count | sum | avg | none",
            "filters": {{"field": "value"}}  # optional
            }}

            Tickets JSON:
            {json.dumps(tickets)}

            User query: "{req.message}"
            """

            resp = client.chat.completions.create(
                model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
                messages=[{"role": "system", "content": system_prompt}],
                temperature=0
            )

            content = resp.choices[0].message.content.strip()

            # Remove markdown fences if present
            if content.startswith("```"):
                parts = content.split("```")
                if len(parts) >= 2:
                    content = parts[1]
            content = content.strip()

            chart_spec = json.loads(content)

            # ------------------ Generate chart ------------------
            df = pd.DataFrame(tickets)

            # Apply filters if provided
            filters = chart_spec.get("filters", {})
            for field, value in filters.items():
                if field in df.columns:
                    df = df[df[field] == value]
                elif "." in field:
                    col1, col2 = field.split(".", 1)
                    df = df[df[col1].apply(lambda x: isinstance(x, dict) and x.get(col2) == value)]

            # Helper to extract column (supports nested fields)
            def extract_column(field):
                if "." in field:
                    col1, col2 = field.split(".", 1)
                    return df[col1].apply(lambda x: x.get(col2) if isinstance(x, dict) else None)
                return df[field]

            x_data = extract_column(chart_spec["x"])

            if chart_spec["aggregation"].lower() == "count":
                plot_data = x_data.value_counts()
                y_data = plot_data.values
                x_labels = plot_data.index
            else:
                y_data = extract_column(chart_spec["y"])
                x_labels = x_data

            plt.figure(figsize=(8, 5))
            chart_type = chart_spec["chart_type"].lower()

            if chart_type == "bar":
                plt.bar(x_labels, y_data)
            elif chart_type == "line":
                plt.plot(x_labels, y_data, marker="o")
            elif chart_type == "pie":
                plt.pie(y_data, labels=x_labels, autopct="%1.1f%%")
            elif chart_type == "histogram":
                plt.hist(y_data, bins=10)

            plt.xlabel(chart_spec["x"])
            plt.ylabel(chart_spec.get("y", "Values"))
            plt.title(req.message)

            # Save plot to base64
            buf = BytesIO()
            plt.savefig(buf, format="png")
            buf.seek(0)
            graph_bytes = buf.read()
            if not graph_bytes:
                raise ValueError("Failed to generate graph image")
            graph_base64 = base64.b64encode(graph_bytes).decode("utf-8")
            plt.close()

            # ------------------ Return response ------------------
            return ChatResponse(
                message="Graph generated successfully",
                status="success",
                chart_spec=chart_spec,
                graph_image=graph_base64
            )

        except Exception as e:
            return ChatResponse(
                message=f"Failed to generate graph: {str(e)}"
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

