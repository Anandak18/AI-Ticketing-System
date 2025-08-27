import os
from openai import AzureOpenAI
import json
import re

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version="2024-02-15-preview",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)

# PLACEHOLDERS = ["TODO", "TBD", "XXX", "...", "placeholder"]

def is_valid_comment(comment: str) -> dict:
    """
    Validate a ticket review comment using LLM. 
    If invalid, generate a corrected version that satisfies all rules.

    Rules:
    1. Must be at least 15 words.
    2. Must describe what changed in the ticket.
    3. Should explain why the change was made (optional but recommended).
    4. Must include at least one actionable step (e.g., deploy, test, monitor, restart, rollback).

    Returns:
        {
            "valid": bool,
            "message": str,
            "corrected_comment": str (optional, only if comment was corrected)
        }
    """
    # Pre-check word count
    word_count = len([w for w in re.split(r'\s+', comment) if w.strip()])
    if word_count < 15:
        return {
            "valid": False,
            "message": f"Comment is too short ({word_count} words). Minimum 15 words required.",
            "corrected_comment": None
        }

    # Build LLM prompt
    messages = [
        {
            "role": "system",
            "content": (
                "You are a ticket review assistant. Validate and, if necessary, correct user comments "
                "for ticket reviews. Rules:\n"
                "1. Must be at least 15 words.\n"
                "2. Must describe what changed in the ticket.\n"
                "3. Should explain why the change was made (optional but recommended).\n"
                "4. Must include at least one actionable step (e.g., deploy, test, monitor, restart, rollback).\n"
                "IMPORTANT: Respond only in valid JSON with keys:\n"
                "  - valid (true/false)\n"
                "  - message (reason if invalid or confirmation if valid)\n"
                "  - corrected_comment (string, only if comment was invalid and corrected)"
            )
        },
        {"role": "user", "content": comment}
    ]

    try:
        resp = client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            messages=messages,
            temperature=0
        )

        llm_output = resp.choices[0].message.content.strip()

        # Extract JSON safely
        match = re.search(r"\{.*\}", llm_output, re.DOTALL)
        if match:
            print("match")
            return json.loads(match.group())
        else:
            return {
                "valid": False,
                "message": "LLM response could not be parsed, please rewrite the comment.",
                "corrected_comment": None
            }
    except Exception as e:
        return {
            "valid": False,
            "message": f"Error validating comment: {str(e)}",
            "corrected_comment": None
        }
