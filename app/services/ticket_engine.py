import json, time, asyncio, datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from .slot_extractor import extract_with_openai
from ..config import TICKETS_PATH, CONFIDENCE_CLOSE_THRESHOLD, POLL_INTERVAL_SECONDS
from ..models.schemas import TicketSlots
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)



def load_json(path: Path):
    if not path.exists():
        return []
    with path.open('r', encoding='utf-8') as f:
        return json.load(f)

def save_json(path: Path, data):
    with path.open('w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def weighted_confidence(result: Dict) -> float:
    scores = result["confidence_scores"]
    return (
        scores["issue_type"] * 0.4 +
        scores["severity"] * 0.3 +
        scores["affected_system"] * 0.3
    )




def propose_fix(result: Dict) -> str:
    it = result['issue_type']
    sev = result['severity']
    sys = result['affected_system']
    return (f"For a {sev} {it} in {sys}, clear caches/restart affected service, "
            f"check recent changes and logs, and validate with a test case. "
            f"If stable, roll to staging then production.")


async def process_tickets_once(tickets_path: Path):
    data = load_json(tickets_path)
    changed = False
    for t in data:
        if t.get('aggregate_confidence') is not None or t.get('status') in ('closed', 'needs-review'):
            continue

        desc = t.get('description', '')
        result = extract_with_openai(desc)

        # agg = weighted_confidence(result)
        t['slots'] = result
        # t['aggregate_confidence'] = round(agg, 4)
        result["aggregate_confidence"]

        logging.info(f"[Ticket {t['ticket_no']}] Aggregate confidence = {result["aggregate_confidence"]}")

        if result["aggregate_confidence"] >= CONFIDENCE_CLOSE_THRESHOLD:
            t['proposedFix'] = propose_fix(result)
            t['status'] = 'closed'
        else:
            t['status'] = 'needs-review'

        t.setdefault('metadata', {})['updatedAt'] = datetime.datetime.utcnow().isoformat() + 'Z'
        changed = True

    if changed:
        save_json(tickets_path, data)
    return changed

async def poller():
    while True:
        logging.info("Checking for new tickets...")
        try:
            await process_tickets_once(TICKETS_PATH)
        except Exception as e:
            # log to console
            print("[poller] error:", e)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
