from fastapi import APIRouter, HTTPException
import asyncio
import matplotlib.pyplot as plt
from io import BytesIO
import base64
import pandas as pd
from pathlib import Path
from datetime import datetime
import json
import os
from openai import AzureOpenAI
from langgraph.graph import StateGraph, END,START
from typing import Dict, List, Optional, TypedDict
from dotenv import load_dotenv
from ..services.comment_validator import is_valid_comment
from ..config import TICKETS_PATH, MEMORY_PATH, CONFIDENCE_CLOSE_THRESHOLD
from ..models.schemas import ChatRequest, ChatResponse
from ..services.ticket_engine import load_json, save_json
from ..services.slot_extractor import extract_with_openai



router = APIRouter()

# ------------------------------
# Azure OpenAI client
# ------------------------------


load_dotenv()

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
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
    
    # Get memory for conversation history
    memory = state["memory"]
    
    # Debug: Print basic info about the data
    print(f"DEBUG - Total tickets: {len(df)}")
    print(f"DEBUG - Unique statuses: {df['status'].unique().tolist()}")
    print(f"DEBUG - Status counts: {df['status'].value_counts().to_dict()}")
    
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

    # Create flattened DataFrame for analysis
    flattened_df = pd.DataFrame(flattened_records)
    print(f"DEBUG - Flattened DataFrame columns: {flattened_df.columns.tolist()}")
    
    # Get basic statistics for different queries
    stats = {
        "total_tickets": len(df),
        "status_breakdown": df['status'].value_counts().to_dict(),
    }
    
    # Add severity breakdown if available
    if 'slots_severity' in flattened_df.columns:
        stats["severity_breakdown"] = flattened_df['slots_severity'].value_counts().to_dict()
        
        # Breakdown by status and severity
        for status in df['status'].unique():
            status_df = flattened_df[flattened_df['status'] == status]
            if len(status_df) > 0 and 'slots_severity' in status_df.columns:
                stats[f"{status}_severity_breakdown"] = status_df['slots_severity'].value_counts().to_dict()
    
    # Add system breakdown if available
    if 'slots_affected_system' in flattened_df.columns:
        stats["system_breakdown"] = flattened_df['slots_affected_system'].value_counts().to_dict()
    
    # Add issue type breakdown if available
    if 'slots_issue_type' in flattened_df.columns:
        stats["issue_type_breakdown"] = flattened_df['slots_issue_type'].value_counts().to_dict()
    
    print(f"DEBUG - Statistics: {stats}")
    
    # Convert to JSON
    tickets_json = json.dumps(flattened_records, default=str)
    json_size = len(tickets_json)
    print(f"DEBUG - JSON size: {json_size} characters")
    
    # Create dynamic system prompt based on available data
    available_fields = list(flattened_df.columns)
    
    
 
    system_prompt = f"""You are a ticket analysis assistant. Answer the user's question based on the provided ticket data.

AVAILABLE DATA FIELDS: {available_fields}

COMPLETE TICKET DATA:
{tickets_json}

SUMMARY STATISTICS FOR REFERENCE:
{json.dumps(stats, indent=2)}

USER QUESTION: {state['message']}

INSTRUCTIONS:
1. Analyze the complete ticket data to answer the user's specific question
2. Be accurate with counts and numbers
3. If the user asks about "approved" tickets, check what status values actually exist
4. If the user asks about specific attributes (severity, system, etc.), analyze the actual data
5. Provide specific examples or details when relevant
6. If you cannot find exactly what they're asking for, explain what similar information is available
7. Format your response clearly and helpfully"""

    # Add conversation history (last 3 turns max)
    if memory:
        history = "\n".join([json.dumps(m) for m in memory[-3:]])
        system_prompt += f"\n\nConversation history (last 3 turns):\n{history}"

    # Query the LLM
    try:
        resp = client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            messages=[{"role": "system", "content": system_prompt}],
            temperature=0.1  # Keep low for consistency but allow slight variation
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

    # Prepare conversation history context
    conversation_context = ""
    if memory:
        recent_history = memory[-3:]  # Last 3 turns
        conversation_context = "\n".join([
            f"Previous: {json.dumps(m)}" for m in recent_history
        ])

    # Enhanced system prompt with better context handling
    system_prompt = f"""
    You are a ticket review assistant. Extract ticket number and review action from the message and conversation context.
    
    CONVERSATION CONTEXT:
    {conversation_context}
    
    CURRENT MESSAGE: "{req_message}"
    
    INSTRUCTIONS:
    - Look for ticket numbers in BOTH the current message AND the conversation context
    - If the current message doesn't have a ticket number, check the recent conversation for context
    - Look for patterns like "TICKET-0105", "update the TICKET-0105", "need to update the TICKET-0105"
    - Extract the action: APPROVE (close/resolve), REJECT (deny), or EDIT (update/modify)
    - If user provides resolution details, that's usually an APPROVE action
    - If user just says "update" or "edit", that's an EDIT action
    
    Respond in JSON format:
    {{
    "ticket_no": "TICKET-0001",
    "action": "APPROVE",
    "comment": "User comment for review"
    }}
    
    EXAMPLES:
    - "need to update the TICKET-0105" + resolution details → APPROVE action
    - "Found the keycloak is not properly configured..." → APPROVE action (resolving issue)
    - "reject TICKET-0105" → REJECT action
    - "edit TICKET-0105 description" → EDIT action
    """

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

    print(f"DEBUG - LLM Response: {content}")

    # Parse model response
    try:
        review_data = json.loads(content)
        ticket_no = review_data.get("ticket_no")
        action = review_data.get("action", "").upper()
        comments = review_data.get("comment", "")
        
        print(f"DEBUG - Parsed: ticket_no={ticket_no}, action={action}, comments={comments}")
        
    except Exception as e:
        print(f"DEBUG - JSON parsing error: {e}")
        state["response"] = """Please use the format:
        {
        "ticket_no": "TICKET-XXXX",
        "action": "APPROVE | REJECT | EDIT",
        "comment": "string (min 15 words, should include what changed and at least one actionable step)"
        }"""
        return state

    # Validate we got a ticket number
    if not ticket_no:
        state["response"] = "Could not identify ticket number. Please specify the ticket number (e.g., TICKET-0105)."
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
        "user_message": req_message,  # Save original user message
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

    # Update the ticket in the list
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
        # Convert tickets into a DataFrame and flatten like in handle_view
        df = state["tickets"] if isinstance(state["tickets"], pd.DataFrame) else pd.DataFrame(state["tickets"])
        
        # Flatten data manually to preserve structure (same as handle_view)
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

        # Create flattened DataFrame for analysis
        flattened_df = pd.DataFrame(flattened_records)
        available_fields = list(flattened_df.columns)
        
        # Get available status values for the LLM
        available_statuses = flattened_df['status'].unique().tolist() if 'status' in flattened_df.columns else []
        
        print(f"DEBUG - Available fields: {available_fields}")
        print(f"DEBUG - Available statuses: {available_statuses}")
        
        system_prompt = f"""
        You are a data visualization assistant.

        Context:
        - You are given the complete tickets dataset in JSON below.
        - You are also given a user query that asks for a chart.
        - Your job is to interpret the query, decide what fields from the tickets JSON are relevant, 
        and return a chart specification in JSON format.

        IMPORTANT - AVAILABLE FIELDS IN THE DATA:
        {available_fields}

        AVAILABLE STATUS VALUES: {available_statuses}

        FIELD MAPPING NOTES:
        - For severity: use "slots_severity" 
        - For affected system: use "slots_affected_system"
        - For issue type: use "slots_issue_type"
        - For status: use "status" (available values: {available_statuses})
        - For ticket number: use "ticket_no"
        
        QUERY INTERPRETATION NOTES:
        - If user asks "closed cases categorising with status" or similar, they want ALL tickets grouped by status (don't add status filter)
        - If user asks "closed cases with severity", they want only closed tickets grouped by severity (add status filter)
        - When categorizing BY a field, don't filter by that same field
        - Be careful about when to apply filters vs when to just group/categorize

        Rules:
        - Do NOT invent data.
        - Use ONLY fields that exist in the tickets JSON from the available fields list above.
        - For status filters, use the EXACT status values from the available status values list above.
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
        {json.dumps(flattened_records, default=str)}

        User query: "{state['message']}"
        """

        resp = client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            messages=[{"role": "system", "content": system_prompt}],
            temperature=0
        )

        content = resp.choices[0].message.content.strip()
        
        # Clean the response
        if content.startswith("```json"):
            content = content[len("```json"):].strip()
        if content.startswith("```"):
            content = content[3:].strip()
        if content.endswith("```"):
            content = content[:-3].strip()

        chart_spec = json.loads(content)
        print(f"DEBUG - Chart spec: {chart_spec}")

        # Use flattened DataFrame for processing
        df = flattened_df.copy()
        
        # Apply filters with intelligent status mapping
        filters = chart_spec.get("filters", {})
        print(f"DEBUG - Original filters: {filters}")
        print(f"DEBUG - Available status values: {df['status'].unique().tolist()}")
        print(f"DEBUG - Original data count: {len(df)}")
        
        # Special handling: if user wants to categorize BY status, don't filter by status
        x_field = chart_spec.get("x", "")
        if x_field == "status" and "status" in filters:
            print("DEBUG - Removing status filter since we're categorizing BY status")
            filters.pop("status", None)
        
        print(f"DEBUG - Filters after smart removal: {filters}")
        
        for field, value in filters.items():
            if field in df.columns:
                # Handle status field with case-insensitive matching
                if field == "status":
                    available_statuses = df['status'].unique()
                    # Try to find matching status (case-insensitive)
                    matched_status = None
                    for status in available_statuses:
                        if str(status).lower() == str(value).lower():
                            matched_status = status
                            break
                    
                    if matched_status:
                        df = df[df[field] == matched_status]
                        print(f"DEBUG - After filtering {field}={value} (matched to {matched_status}): {len(df)} records")
                    else:
                        print(f"DEBUG - Status '{value}' not found. Available: {available_statuses}")
                        # If no match, try common mappings
                        status_mappings = {
                            'closed': ['CLOSED', 'RESOLVED', 'COMPLETED'],
                            'open': ['OPEN', 'NEW', 'IN_PROGRESS'],
                            'approved': ['APPROVED', 'APPROVE'],
                            'rejected': ['REJECTED', 'REJECT']
                        }
                        
                        mapped_statuses = status_mappings.get(value.lower(), [])
                        found_status = None
                        for mapped in mapped_statuses:
                            if mapped in available_statuses:
                                found_status = mapped
                                break
                        
                        if found_status:
                            df = df[df[field] == found_status]
                            print(f"DEBUG - After mapping {value} to {found_status}: {len(df)} records")
                        else:
                            print(f"DEBUG - Could not map status '{value}' to any available status")
                else:
                    # Regular filtering for non-status fields
                    df = df[df[field] == value]
                    print(f"DEBUG - After filtering {field}={value}: {len(df)} records")

        print(f"DEBUG - Final data count after all filters: {len(df)}")

        # Extract data for plotting
        x_field = chart_spec["x"]
        
        if x_field not in df.columns:
            raise ValueError(f"Field '{x_field}' not found in data. Available fields: {list(df.columns)}")
            
        x_data = df[x_field].dropna()
        print(f"DEBUG - X data value counts: {x_data.value_counts()}")

        if chart_spec["aggregation"].lower() == "count":
            plot_data = x_data.value_counts()
            y_data = plot_data.values
            x_labels = plot_data.index
        else:
            y_field = chart_spec["y"]
            if y_field not in df.columns:
                raise ValueError(f"Field '{y_field}' not found in data. Available fields: {list(df.columns)}")
            y_data = df[y_field].dropna()
            x_labels = x_data

        plt.figure(figsize=(10, 6))
        chart_type = chart_spec["chart_type"].lower()
        print(f"DEBUG - Plotting {chart_type} with x_labels: {x_labels}, y_data: {y_data}")
        
        if chart_type == "bar":
            plt.bar(range(len(x_labels)), y_data)
            plt.xticks(range(len(x_labels)), x_labels, rotation=45)
        elif chart_type == "line":
            plt.plot(range(len(x_labels)), y_data, marker="o")
            plt.xticks(range(len(x_labels)), x_labels, rotation=45)
        elif chart_type == "pie":
            plt.pie(y_data, labels=x_labels, autopct="%1.1f%%")
        elif chart_type == "histogram":
            plt.hist(y_data, bins=10)

        plt.xlabel(chart_spec["x"])
        plt.ylabel(chart_spec.get("y", "Count"))
        plt.title(state["message"])
        plt.tight_layout()  # Better layout handling

        buf = BytesIO()
        plt.savefig(buf, format="png", dpi=300, bbox_inches='tight')
        buf.seek(0)
        graph_bytes = buf.read()
        graph_base64 = base64.b64encode(graph_bytes).decode("utf-8")
        plt.close()

        state["response"] = f"Graph generated successfully. Showing {len(df)} records."
        state["chart_spec"] = chart_spec
        state["graph_image"] = graph_base64
        return state

    except Exception as e:
        print(f"DEBUG - Error details: {str(e)}")
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
# @router.post("/chat", response_model=ChatResponse)
# def chat(req: ChatRequest):
#     try:
#         tickets = load_json(TICKETS_PATH)
#         memory = load_json(MEMORY_PATH)
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Failed to load data: {str(e)}")

#     state = {
#         "message": req.message,
#         "tickets": tickets,
#         "memory": memory,
#         "response": "",
#         "chart_spec": None,
#         "graph_image": None,
#         "intent": None
#     }

#     result = app_graph.invoke(state)

#     # save chat
#     memory_entry = {
#         "user_message": req.message,
#         "bot_response": result.get("response", ""),
#         "timestamp": datetime.utcnow().isoformat() + 'Z'
#     }
#     memory.append(memory_entry)
#     save_json(MEMORY_PATH, memory)

#     return ChatResponse(
#         message=result.get("response", ""),
#         status="success",
#         chart_spec=result.get("chart_spec"),
#         graph_image=result.get("graph_image")
#     )

@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        tickets, memory = await asyncio.gather(
            asyncio.to_thread(load_json, TICKETS_PATH),
            asyncio.to_thread(load_json, MEMORY_PATH),
        )
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

    # if app_graph has ainvoke, prefer it
    if hasattr(app_graph, "ainvoke"):
        result = await app_graph.ainvoke(state)
    else:
        result = await asyncio.to_thread(app_graph.invoke, state)

    # save chat
    memory_entry = {
        "user_message": req.message,
        "bot_response": result.get("response", ""),
        "timestamp": datetime.utcnow().isoformat() + 'Z'
    }
    memory.append(memory_entry)
    await asyncio.to_thread(save_json, MEMORY_PATH, memory)

    return ChatResponse(
        message=result.get("response", ""),
        status="success",
        chart_spec=result.get("chart_spec"),
        graph_image=result.get("graph_image")
    )