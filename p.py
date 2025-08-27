# routes/chat.py

from fastapi import APIRouter, HTTPException, Depends
from ..models.schemas import ChatRequest, ChatResponse
from ..agents.ticket_agents import TicketingAgentOrchestrator
from ..config import OPENAI_API_KEY
import asyncio
import logging

router = APIRouter()

# Global agent orchestrator instance
agent_orchestrator = None

def get_agent_orchestrator():
    """Dependency to get or create the agent orchestrator"""
    global agent_orchestrator
    if agent_orchestrator is None:
        if not OPENAI_API_KEY:
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")
        agent_orchestrator = TicketingAgentOrchestrator(OPENAI_API_KEY)
    return agent_orchestrator

@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest, 
    orchestrator: TicketingAgentOrchestrator = Depends(get_agent_orchestrator)
):
    """
    Main chat endpoint that uses LangGraph agents to handle all interactions:
    - Intent classification
    - Ticket creation with LLM-based slot extraction  
    - Ticket querying and filtering
    - Review workflow with comment validation
    - Memory management for interactions
    """
    
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    try:
        # Process the message through the agent workflow
        result = await orchestrator.process_message(req.message.strip())
        
        # Build response
        response_data = {
            "message": result["message"]
        }
        
        # Add ticket information if available
        if result.get("ticket_data"):
            ticket_data = result["ticket_data"]
            
            if "ticket" in ticket_data:  # Single ticket (creation)
                ticket = ticket_data["ticket"]
                response_data["updatedTicket"] = {
                    "ticketNo": ticket.get("ticket_no"),
                    "description": ticket.get("description"),
                    "status": ticket.get("status"),
                    "confidence": ticket.get("slots", {}).get("confidence_scores", {}).get("issue_type", 0.0) if ticket.get("slots") else None,
                    "issueType": ticket.get("slots", {}).get("issue_type") if ticket.get("slots") else None,
                    "severity": ticket.get("slots", {}).get("severity") if ticket.get("slots") else None,
                    "affectedSystem": ticket.get("slots", {}).get("affected_system") if ticket.get("slots") else None
                }
            
            elif "tickets" in ticket_data:  # Multiple tickets (query)
                tickets = ticket_data["tickets"][:10]  # Limit for performance
                response_data["tickets"] = [
                    {
                        "ticketNo": t.get("ticket_no"),
                        "description": t.get("description"),
                        "status": t.get("status"),
                        "severity": t.get("slots", {}).get("severity") if t.get("slots") else "Unknown",
                        "issueType": t.get("slots", {}).get("issue_type") if t.get("slots") else "Unknown",
                        "createdAt": t.get("metadata", {}).get("createdAt")
                    }
                    for t in tickets
                ]
        
        # Handle validation results for comments
        if result.get("validation_result"):
            validation = result["validation_result"]
            response_data["validationResult"] = {
                "valid": validation["valid"],
                "message": validation["validation_message"]
            }
            
            if validation["valid"]:
                response_data["extractedData"] = validation.get("extracted_data", {})
        
        # Indicate if human input is needed
        if result.get("needs_human_input"):
            response_data["needsHumanInput"] = True
        
        return ChatResponse(**response_data)
        
    except Exception as e:
        logging.error(f"Error in chat endpoint: {str(e)}")
        
        # Provide helpful error messages based on the error type
        if "OpenAI" in str(e) or "API" in str(e):
            raise HTTPException(
                status_code=503, 
                detail="AI service temporarily unavailable. Please try again."
            )
        elif "JSON" in str(e):
            raise HTTPException(
                status_code=422,
                detail="Error processing your request. Please rephrase your message."
            )
        else:
            raise HTTPException(
                status_code=500, 
                detail=f"An error occurred while processing your request: {str(e)}"
            )

@router.post("/chat/validate-comments")
async def validate_comments(
    ticket_no: str,
    comments: str,
    orchestrator: TicketingAgentOrchestrator = Depends(get_agent_orchestrator)
):
    """
    Separate endpoint for comment validation during review workflow
    """
    try:
        # Use the ticket reviewer agent directly
        validation = orchestrator.ticket_reviewer.validate_comments(comments)
        
        return {
            "valid": validation["valid"],
            "message": validation["validation_message"],
            "extractedData": validation.get("extracted_data") if validation["valid"] else None
        }
        
    except Exception as e:
        logging.error(f"Error validating comments: {str(e)}")
        raise HTTPException(status_code=500, detail="Error validating comments")

@router.get("/chat/system-status")
async def get_system_status(
    orchestrator: TicketingAgentOrchestrator = Depends(get_agent_orchestrator)
):
    """
    Get current system status and ticket statistics
    """
    try:
        # Query all tickets for status
        query_result = orchestrator.tool_executor.invoke({
            "tool_name": "query_tickets",
            "tool_input": {}
        })
        
        tickets = query_result.get("tickets", [])
        
        # Calculate statistics
        stats = {
            "total": len(tickets),
            "open": len([t for t in tickets if t.get("status") == "open"]),
            "closed": len([t for t in tickets if t.get("status") == "closed"]),
            "needs_review": len([t for t in tickets if t.get("status") == "needs-review"]),
            "high_severity": len([t for t in tickets if t.get("slots", {}).get("severity") == "High"]),
            "medium_severity": len([t for t in tickets if t.get("slots", {}).get("severity") == "Medium"]),
            "low_severity": len([t for t in tickets if t.get("slots", {}).get("severity") == "Low"])
        }
        
        return {
            "status": "online",
            "statistics": stats,
            "message": f"System operational. {stats['total']} total tickets, {stats['needs_review']} need review."
        }
        
    except Exception as e:
        logging.error(f"Error getting system status: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrieving system status")

# Health check endpoint
@router.get("/chat/health")
async def health_check():
    """Simple health check endpoint"""
    return {"status": "healthy", "message": "Chat API is operational"}

# Memory endpoint for debugging/admin
@router.get("/chat/memory")
async def get_memory(
    ticket_id: str = None,
    orchestrator: TicketingAgentOrchestrator = Depends(get_agent_orchestrator)
):
    """
    Get memory entries, optionally filtered by ticket ID