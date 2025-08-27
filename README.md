<<<<<<< HEAD
# Automated Ticketing Solution — FastAPI Backend

This backend implements the POC described in the provided document.

## How to run

```bash
python -m venv .venv && . .venv/Scripts/activate  # on Windows
# or: source .venv/bin/activate  # on Linux/Mac

pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload
```

Environment variables:
- `OPENAI_API_KEY` (optional) — used for slot extraction via OpenAI. If absent, a simple rule-based fallback is used.
- `TICKETS_PATH` (optional) — path to `tickets.json` (defaults to project `data/tickets.json`).
- `MEMORY_PATH` (optional) — path to `memory.json` (defaults to project `data/memory.json`).

=======
# AI-Powered Ticketing System

An automated IT ticket management solution powered by **Azure OpenAI**, **FastAPI**, and **Streamlit**.  
The system automatically classifies, prioritizes, and resolves IT support tickets.  
It combines backend intelligence (FastAPI + Azure OpenAI) with a simple frontend (Streamlit chat UI).  

---

## Features
- **Smart Ticket Classification** → Extracts:
  - `issue_type` → bug, outage, incident, request, change  
  - `severity` → low, medium, high, critical  
  - `affected_system` → CRM, ERP, Email, Network, Database, Mobile App, Web Portal, etc.  
- **Confidence Scoring** → Auto-closes tickets when AI confidence ≥ 85%, else escalates for review.  
- **Proposed Fix Generator** → Suggests possible fixes for high-confidence cases.  
- **FastAPI Chatbot API** → Create tickets, check status, and perform review actions.  
- **Streamlit Frontend** → Chat-like interface for ticket interaction.  
- **Continuous Processing** → Reads tickets from JSON every 2 minutes and updates automatically.  
- **Human-in-the-loop Review** → Users can approve, edit, or reject AI-generated resolutions.  

---

## Project Structure
```
ai-ticketing-system/
│
├── app/                       # Core application logic
│   ├── classifier.py          # Azure OpenAI ticket classification
│   ├── resolver.py            # Automated fix suggestion generator
│   ├── processor.py           # Pipeline (reads tickets.json, updates status)
│   ├── scheduler.py           # Runs process every 2 minutes
│   └── utils.py               # Helper functions
│
├── api/                       # FastAPI backend
│   ├── main.py                # FastAPI entry point
│   ├── routes/
│   │   ├── chat.py            # /api/chat endpoint
│   │   └── tickets.py         # /api/tickets endpoints
│   └── models/
│       └── ticket.py          # Pydantic models
│
├── web/                       # Streamlit frontend
│   └── app.py                 # Chat UI
│
├── data/                      # Data storage
│   ├── tickets.json           # Input & updated tickets
│   └── memory.json            # Stores user-approved comments/resolutions
│
├── tests/                     # Unit & integration tests
│
├── .env                       # Azure OpenAI credentials
├── .gitignore                 # Ignore venv, cache, etc.
├── requirements.txt           # Python dependencies
├── run.py                     # Project entry point (pipeline runner)
└── README.md                  # Documentation
```

---

## Setup Instructions

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/ai-ticketing-system.git
cd ai-ticketing-system
```

### 2. Create a Virtual Environment
```bash
python -m venv venv
source venv/bin/activate   # Linux/Mac
venv\Scripts\activate      # Windows
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Azure OpenAI
Create a `.env` file in the root folder:

```ini
AZURE_OPENAI_KEY=your_api_key_here
AZURE_OPENAI_ENDPOINT=https://your-resource-name.openai.azure.com/
```

---

## Running the Project

### 1. Run Ticket Processing Pipeline
```bash
python run.py
```
- Reads `tickets.json` every 2 minutes  
- Classifies tickets and updates status:  
  - **closed** → AI confident fix  
  - **needs-review** → Escalated for human input  

### 2. Run FastAPI Backend
```bash
uvicorn api.main:app --reload --port 8000
```
- API available at → `http://localhost:8000`

**Endpoints:**
- `GET /api/tickets` → List all tickets  
- `POST /api/tickets` → Create new ticket  
- `POST /api/chat` → Chat with AI assistant  

### 3. Run Streamlit Frontend
```bash
streamlit run web/app.py
```
- UI available at → `http://localhost:8501`  

---

##  Example Workflow
1. User creates a ticket:  
   *“The billing page freezes when I click Apply Discount.”*  
2. AI extracts slots:  
   - `issue_type=bug`  
   - `severity=medium`  
   - `affected_system=Web Portal`  
3. Confidence = 90% → AI suggests a fix → marks ticket **closed**.  
4. Confidence = 70% → ticket marked **needs-review** → user provides resolution via UI/API.  
5. Approved resolution is stored in `memory.json` for audit & learning.  

---

## API Examples

### Create a Ticket
```bash
curl -X POST "http://localhost:8000/api/tickets" -H "Content-Type: application/json" -d '{"description": "The CRM keeps logging me out randomly."}'
```

### Check All Tickets
```bash
curl "http://localhost:8000/api/tickets"
```

### Chat with System
```bash
curl -X POST "http://localhost:8000/api/chat" -H "Content-Type: application/json" -d '{"message": "Show me all high severity tickets"}'
```

---

## Future Enhancements
- Integrate with **PostgreSQL/MySQL** instead of JSON  
- Connect to **Jira / ServiceNow** for enterprise ticketing  
- Add **analytics dashboard** (Plotly/Streamlit)  
- Provide **Docker Compose** setup for FastAPI + Streamlit deployment  
- Add **multi-agent workflow** (classification agent, fix generator, reviewer agent)  

---

## Contributing
Pull requests are welcome! Please fork the repo and create a feature branch.  

---

## License
MIT License – free for personal and commercial use.  
>>>>>>> 47547fa1c316d78786843bdbc14963ba250367ae
