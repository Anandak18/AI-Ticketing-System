from fastapi import APIRouter, HTTPException
from ..models.schemas import ChatRequest, ChatResponse
from ..services.ticket_engine import load_json, save_json
from ..services.slot_extractor import extract_with_openai
from pathlib import Path
from datetime import datetime
import json
import os
from openai import AzureOpenAI
from typing import Dict, List, Optional, TypedDict
from ..services.comment_validator import is_valid_comment
from ..config import TICKETS_PATH, MEMORY_PATH, CONFIDENCE_CLOSE_THRESHOLD
import matplotlib.pyplot as plt
from io import BytesIO
import base64
import pandas as pd

# LangGraph
from langgraph.graph import StateGraph, END,START

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

3. review -> when the user wants to modify or review the ticket to approve/reject/edit a ticket
4. delete -> when the user asks to delete a ticket
5. graph -> when the user explicitly asks for a chart, graph, plot, visualization, diagram, or trend.

Always return only one word: create, view, review, delete, or graph.

Message: "{message}"
"""
    resp = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    
    return resp.choices[0].message.content.strip().lower()

# ------------------------------
# LangGraph State
# ------------------------------
class TicketState(TypedDict):
    message: str
    tickets: list
    memory: list
    response: str
    chart_spec: Optional[dict]
    graph_image: Optional[str]
    intent: Optional[str]

# ------------------------------
# Router node
# ------------------------------
def route_intent(state: TicketState):
    intent = llm_intent(state["message"])
    print("Detected intent:", intent)
    state["intent"] = intent
    return state
# ------------------------------
# Handlers (your existing logic lifted here unchanged)
# ------------------------------

def handle_create(state: TicketState):
    print("create")
    req_message = state["message"]
    tickets = state["tickets"]

    if ":" in req_message:
        desc = req_message.split(":", 1)[1].strip().strip('"').strip("'")
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

            slots = extract_with_openai(desc)

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

    state["response"] = response_message
    return state


def handle_view(state: TicketState):
    print("view-------")

    # Convert tickets into a DataFrame if not already
    df = state["tickets"] if isinstance(state["tickets"], pd.DataFrame) else pd.DataFrame(state["tickets"])
    
    # DEBUG: Print actual counts before sending to LLM
    closed_tickets = df[df['status'] == 'closed']
    print(f"DEBUG - Total closed tickets: {len(closed_tickets)}")
    
    if 'slots' in df.columns:
        severity_counts = closed_tickets['slots'].apply(
            lambda x: x.get('severity', 'unknown') if isinstance(x, dict) else 'unknown'
        ).value_counts()
        print(f"DEBUG - Actual severity counts: {severity_counts.to_dict()}")

    # Flatten data manually to preserve structure
    flattened_records = []
    for _, row in df.iterrows():
        flat_record = {}
        for col, value in row.items():
            if col in ["slots", "metadata"] and isinstance(value, dict):
                for nested_key, nested_value in value.items():
                    flat_record[f"{col}_{nested_key}"] = nested_value
            else:
                flat_record[col] = value
        flattened_records.append(flat_record)

    # DEBUG: Check flattened data integrity
    flattened_df = pd.DataFrame(flattened_records)
    if 'slots_severity' in flattened_df.columns:
        flattened_closed = flattened_df[flattened_df['status'] == 'closed']
        flattened_severity_counts = flattened_closed['slots_severity'].value_counts()
        print(f"DEBUG - Flattened severity counts: {flattened_severity_counts.to_dict()}")

    # Convert to JSON
    tickets_json = json.dumps(flattened_records, default=str)
    
    # DEBUG: Check JSON size
    json_size = len(tickets_json)
    print(f"DEBUG - JSON size: {json_size} characters")
    
    # If JSON is too large, provide summary instead
    if json_size > 50000:  # Adjust threshold as needed
        system_prompt = f"""You are a ticket analysis assistant. 

SUMMARY STATISTICS (Use these for counting):
- Total tickets: {len(df)}
- Total closed tickets: {len(closed_tickets)}
- Closed tickets by severity: {severity_counts.to_dict() if 'slots' in df.columns else 'N/A'}

SAMPLE DATA (for structure reference):
{json.dumps(flattened_records[:5], indent=2, default=str)}

USER QUESTION: {state['message']}

Use the summary statistics for accurate counts. The sample data shows the structure but use the statistics above for numerical answers."""
    else:
        system_prompt = f"""You are a ticket analysis assistant. Answer based on the provided data.

TICKET DATA:
{tickets_json}

USER QUESTION: {state['message']}

When counting, be systematic and accurate."""

    # Query the LLM
    try:
        resp = client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            messages=[{"role": "system", "content": system_prompt}],
            temperature=0
        )
        
        state["response"] = resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"LLM Error: {e}")
        state["response"] = f"Error processing request: {str(e)}"
    
    return state

def handle_review(state: TicketState):
    print("review")
    req_message = state["message"]
    tickets = state["tickets"]
    memory = state["memory"]

    # System prompt
    system_prompt = f"""
    Extract ticket number and review action (APPROVE, REJECT, EDIT) from the message.
    Respond in JSON format:
    {{
    "ticket_no": "TICKET-0001",
    "action": "APPROVE",
    "comment": "User comment for review"
    }}
    Message: "{req_message}"
    """

    # Add conversation history (last 3 turns max)
    if memory:
        history = "\n".join([json.dumps(m) for m in memory[-3:]])
        system_prompt += f"\nConversation history:\n{history}"

    # Call model
    resp = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        messages=[{"role": "user", "content": system_prompt}],
        temperature=0
    )
    content = resp.choices[0].message.content.strip()

    # Clean response
    if content.startswith("```json"):
        content = content[len("```json"):].strip()
    if content.startswith("```"):
        content = content[3:].strip()
    if content.endswith("```"):
        content = content[:-3].strip()

    # Parse model response
    try:
        review_data = json.loads(content)
        ticket_no = review_data.get("ticket_no")
        action = review_data.get("action", "").upper()
        comments = review_data.get("comment", "")
    except Exception:
        state["response"] = """Please use the format:
        {
        "ticketNo": "string",
        "action": "APPROVE | REJECT | EDIT",
        "comments": "string (min 15 words, should include what changed and at least one actionable step)"
        }"""
        return state

    # Ticket lookup
    ticket = next((t for t in tickets if t.get("ticket_no") == ticket_no), None)
    if not ticket:
        state["response"] = f"Ticket {ticket_no} not found."
        return state

    # Validate action
    if action not in ("APPROVE", "REJECT", "EDIT"):
        state["response"] = f"Invalid action '{action}'. Use APPROVE, REJECT, or EDIT."
        return state

    # Validate comment
    validation = is_valid_comment(comments)
    if not validation.get("valid"):
        state["response"] = validation.get("message")
        return state

    # Build entry and save to memory
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

    # Update ticket
    ticket["status"] = {
        "APPROVE": "APPROVED",
        "REJECT": "REJECTED",
        "EDIT": "EDITED"
    }[action]

    ticket.setdefault("metadata", {})["lastReviewAction"] = action
    ticket["metadata"]["updatedAt"] = datetime.utcnow().isoformat() + 'Z'
    ticket["review_summary"] = comments.split('.')[0].strip()
    ticket["resolution_steps"] = comments.strip()

    for i, t in enumerate(tickets):
        if t.get("ticket_no") == ticket_no:
            tickets[i] = ticket
            break

    save_json(TICKETS_PATH, tickets)

    state["response"] = (
        f"Ticket {ticket_no} reviewed successfully.\n"
        f"Status: {ticket['status']}\n"
        f"Summary: {ticket['review_summary']}"
    )
    return state


def handle_delete(state: TicketState):
    print("delete")
    state["response"] = "You can view and edit the ticket but the deletion is not allowed."
    return state


def handle_graph(state: TicketState):
    print("graph")
    try:
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
        "filters": {{"field": "value"}}
        }}

        Tickets JSON:
        {json.dumps(state['tickets'])}

        User query: "{state['message']}"
        """

        resp = client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            messages=[{"role": "system", "content": system_prompt}],
            temperature=0
        )

        content = resp.choices[0].message.content.strip()
        if content.startswith("```"):
            parts = content.split("```")
            if len(parts) >= 2:
                content = parts[1]
        content = content.strip()

        chart_spec = json.loads(content)

        df = pd.DataFrame(state["tickets"])
        filters = chart_spec.get("filters", {})
        for field, value in filters.items():
            if field in df.columns:
                df = df[df[field] == value]
            elif "." in field:
                col1, col2 = field.split(".", 1)
                df = df[df[col1].apply(lambda x: isinstance(x, dict) and x.get(col2) == value)]

        def extract_column(field):
            if "." in field:
                col1, col2 = field.split(".", 1)
                return df[col1].apply(lambda x: x.get(col2) if isinstance(x, dict) else None)
            return df[field]

        x_data = extract_column(chart_spec["x"])
        print(x_data)

        if chart_spec["aggregation"].lower() == "count":
            plot_data = x_data.value_counts()
            y_data = plot_data.values
            x_labels = plot_data.index
        else:
            y_data = extract_column(chart_spec["y"])
            x_labels = x_data

        plt.figure(figsize=(8, 5))
        chart_type = chart_spec["chart_type"].lower()
        print("x_labels,y_data",x_labels,y_data)
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
        plt.title(state["message"])

        buf = BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        graph_bytes = buf.read()
        graph_base64 = base64.b64encode(graph_bytes).decode("utf-8")
        plt.close()

        state["response"] = "Graph generated successfully"
        state["chart_spec"] = chart_spec
        state["graph_image"] = graph_base64
        return state

    except Exception as e:
        state["response"] = f"Failed to generate graph: {str(e)}"
        return state


# ------------------------------
# Build LangGraph workflow
# ------------------------------
workflow = StateGraph(TicketState)

# Add nodes
workflow = StateGraph(TicketState)

# Add nodes
workflow.add_node("route", route_intent)
workflow.add_node("create", handle_create)
workflow.add_node("view", handle_view)
workflow.add_node("review", handle_review)
workflow.add_node("delete", handle_delete)
workflow.add_node("graph", handle_graph)

# Entry point
workflow.add_edge(START, "route")

# Conditional routing
workflow.add_conditional_edges(
    "route",
    lambda state: state["intent"]
)

# End states
workflow.add_edge("create", END)
workflow.add_edge("view", END)
workflow.add_edge("review", END)
workflow.add_edge("delete", END)
workflow.add_edge("graph", END)

# Compile graph
app_graph = workflow.compile()

# Compile graph
app_graph = workflow.compile()
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

    state = {
        "message": req.message,
        "tickets": tickets,
        "memory": memory,
        "response": "",
        "chart_spec": None,
        "graph_image": None,
        "intent": None
    }

    result = app_graph.invoke(state)

    # save chat
    memory_entry = {
        "user_message": req.message,
        "bot_response": result.get("response", ""),
        "timestamp": datetime.utcnow().isoformat() + 'Z'
    }
    memory.append(memory_entry)
    save_json(MEMORY_PATH, memory)

    return ChatResponse(
        message=result.get("response", ""),
        status="success",
        chart_spec=result.get("chart_spec"),
        graph_image=result.get("graph_image")
    )
