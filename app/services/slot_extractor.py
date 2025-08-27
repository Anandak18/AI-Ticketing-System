from typing import Dict
from dotenv import load_dotenv
import os, json
from openai import AzureOpenAI

load_dotenv()
# -----------------------------
# Keyword dictionaries
# -----------------------------
ISSUE_TYPES = {
    'bug': ['bug', 'error', 'broken', 'fails', 'crash', 'hangs'],
    'incident': ['incident', 'outage', 'down', 'offline'],
    'service request': ['service request', 'request', 'whitelist', 'provision'],
    'change': ['change', 'update', 'schema', 'migrations', 'patch']
}

SYSTEMS = [
    'crm', 'erp', 'email system', 'database', 'network',
    'web portal', 'mobile app', 'api', 'reporting module',
    'authentication service'
]

# -----------------------------
# Helpers
# -----------------------------
def fuzzy_find(hay: str, needles):
    hay_l = hay.lower()
    for n in needles:
        if n in hay_l:
            return n
    return "unknown"

def calculate_aggregate(conf: Dict) -> float:
    """Weighted average aggregate confidence."""
    return round(
        conf.get("issue_type", 0) * 0.5 +
        conf.get("severity", 0) * 0.25 +
        conf.get("affected_system", 0) * 0.25,
        2
    )

# -----------------------------
# Fallback extractor
# -----------------------------
def fallback_extract(description: str) -> Dict:

    desc = description.lower()

    issue_type = "bug"
    for k, words in ISSUE_TYPES.items():
        if any(w in desc for w in words):
            issue_type = k
            break

    severity = "low"
    for level in ["critical", "high", "medium", "low"]:
        if level in desc:
            severity = level
            break

    affected = fuzzy_find(desc, SYSTEMS)

    confidence_scores = {
        "issue_type": 0.9 if issue_type != "unknown" else 0.5,
        "severity": 0.95 if severity != "low" else 0.6,
        "affected_system": 0.9 if affected != "unknown" else 0.5,
    }

    return {
        "issue_type": issue_type,
        "severity": severity,
        "affected_system": affected,
        "confidence_scores": confidence_scores,
        "aggregate_confidence": calculate_aggregate(confidence_scores)
    }

# -----------------------------
# Azure OpenAI extractor
# -----------------------------
def extract_with_openai(description: str) -> Dict:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT",None)
    api_key = os.getenv("AZURE_OPENAI_API_KEY",None)
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT",None)

    print("ENV:", endpoint, api_key, deployment, os.getenv("AZURE_API_VERSION"))

    if not endpoint or not api_key or not deployment:
        return fallback_extract(description)

    client = AzureOpenAI(
        api_key=api_key,
        api_version=os.getenv("AZURE_API_VERSION"),
        azure_endpoint=endpoint
    )

    prompt = f"""
    Extract the following information from this IT ticket description:
    - issue_type: one of [bug, incident, service request, change, outage]
    - severity: one of [low, medium, high, critical]
    - affected_system: the main system mentioned [CRM, ERP, Email System, Database, Network, Web Portal, Mobile App, API, Reporting Module, Authentication Service, or other]

    For each field, also provide a confidence score from 0.0 to 1.0.

    Ticket description: "{description}"

    Respond in JSON format:
    {{
        "issue_type": "...",
        "severity": "...",
        "affected_system": "...",
        "confidence_scores": {{
            "issue_type": 0.8,
            "severity": 0.9,
            "affected_system": 0.7
        }}
    }}
    """

    try:
        completion = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": "You are an IT ticket classification expert. Extract information accurately and provide confidence scores."},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            max_tokens=300
        )

        text = completion.choices[0].message.content
        data = json.loads(text)

        if "confidence_scores" not in data:
            raise ValueError("Missing confidence_scores in response")

        data["aggregate_confidence"] = calculate_aggregate(data["confidence_scores"])
        return data

    except Exception as e:
        print(f"[extract_with_openai] error: {e}")
        return fallback_extract(description)
