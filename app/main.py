from fastapi import FastAPI
from .routes import chat, tickets
from .services.ticket_engine import poller
import asyncio

app = FastAPI(title="Automated Ticketing Solution API", version="0.1.0" )

app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(tickets.router, prefix="/api", tags=["tickets"])


@app.on_event("startup")
async def startup_event():
    # kick off background poller
    asyncio.create_task(poller())

@app.get("/health")
def health():
    return {"status":"ok"}
