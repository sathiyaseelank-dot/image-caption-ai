#!/usr/bin/env python3
"""
Google Flow Approval & Decision Consultant
=========================================
Monitors Google Flow for pending generation approvals and credit prompts.
When a prompt is detected:
  1. Extracts the exact request, model, and credit cost.
  2. Notifies the user / AGY with current status.
  3. Consults ChatGPT coworker (or asks the user directly) for approval evaluation.
  4. Only triggers approval in Google Flow when explicit approval is confirmed.

Usage:
  python3 approval_consultant.py --check
  python3 approval_consultant.py --evaluate-with-chatgpt
  python3 approval_consultant.py --confirm-approve
  python3 approval_consultant.py --confirm-reject
"""

import sys
import os
import json
import time
import argparse
from typing import Dict, Any, Optional, List

from google_flow_coworker import (
    find_google_flow_page,
    run_orca_eval,
    get_project_status,
    handle_approval,
    GoogleFlowError
)
from chatgpt_coworker import ask_chatgpt


def detect_pending_approval(page_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Detects if Google Flow has an active, clickable approval prompt.
    Returns details on cost, prompt message, and available action buttons.
    """
    if not page_id:
        page_id = find_google_flow_page()

    js = """
    (() => {
        const agentRows = Array.from(document.querySelectorAll('.agent-row, .model-turn'));
        if (agentRows.length === 0) {
            return JSON.stringify({ hasPending: false, reason: "No agent rows found" });
        }

        const lastRow = agentRows[agentRows.length - 1];
        const rowText = (lastRow.innerText || '').trim();

        // Check for clickable, active radios or buttons
        const activeControls = Array.from(lastRow.querySelectorAll('[role="radio"], button')).filter(el => {
            const text = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
            const isChoice = text.includes('approve') || text.includes('reject');
            const isClickable = !el.hasAttribute('disabled') && el.getAttribute('aria-disabled') !== 'true';
            return isChoice && isClickable;
        }).map(el => ({
            tag: el.tagName,
            text: el.innerText.trim().replace(/\\n/g, ' '),
            ariaLabel: el.getAttribute('aria-label') || ''
        }));

        // Parse credit cost & action proposal
        const costMatch = rowText.match(/costing\\s+(\\d+)\\s+credits?/i);
        const creditCost = costMatch ? parseInt(costMatch[1], 10) : null;

        const isApprovalPrompt = (rowText.includes('Would you like me to') || 
                                  rowText.includes('credits?') || 
                                  rowText.includes('Approve')) && 
                                  activeControls.length > 0;

        return JSON.stringify({
            hasPending: isApprovalPrompt,
            creditCost: creditCost,
            requestText: rowText.replace(/thumb_up\\s*thumb_down\\s*content_copy\\s*flag/g, '').trim(),
            activeControls: activeControls
        });
    })()
    """
    raw = run_orca_eval(js, page_id=page_id)
    try:
        data = json.loads(raw)
        data["browserPageId"] = page_id
        return data
    except Exception as e:
        raise GoogleFlowError(f"Failed to detect approval: {e}")


def consult_chatgpt_for_approval(request_details: Dict[str, Any], story_context: Optional[str] = None) -> Dict[str, Any]:
    """
    Consults ChatGPT whether the pending approval should be granted.
    """
    req_text = request_details.get("requestText", "")
    cost = request_details.get("creditCost")
    context_str = f"\nStory / Scene Intent:\n\"{story_context}\"" if story_context else ""

    prompt = f"""
You are the Executive Producer and Quality Director for an AI video production in Google Flow.
Google Flow is requesting credit approval to generate an asset.

PROPOSAL FROM GOOGLE FLOW:
\"{req_text}\"
Credit Cost: {cost if cost is not None else 'Unspecified'} credits{context_str}

Evaluate if this generation is appropriate, high-quality, and worth the credits.
Decide:
1. DECISION: Must be either "APPROVE" or "REJECT".
2. REASON: 1-2 concise sentences explaining why.

Respond in JSON ONLY:
{{
  "decision": "APPROVE" or "REJECT",
  "reason": "..."
}}
"""
    print("🤖 Consulting ChatGPT coworker on approval decision...")
    response_text = ask_chatgpt(prompt, timeout=60)
    
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        data = json.loads(cleaned)
        decision = data.get("decision", "REJECT").upper()
        return {
            "decision": "APPROVE" if "APPROVE" in decision else "REJECT",
            "reason": data.get("reason", cleaned)
        }
    except Exception:
        is_approve = "APPROVE" in cleaned.upper() and "REJECT" not in cleaned.upper()
        return {
            "decision": "APPROVE" if is_approve else "REJECT",
            "reason": cleaned
        }


def main():
    parser = argparse.ArgumentParser(description="Google Flow Approval & Decision Consultant")
    parser.add_argument("--check", action="store_true", help="Check if an approval prompt is pending")
    parser.add_argument("--evaluate-with-chatgpt", action="store_true", help="Consult ChatGPT on whether to approve")
    parser.add_argument("--confirm-approve", action="store_true", help="Execute approve click in Google Flow")
    parser.add_argument("--confirm-reject", action="store_true", help="Execute reject click in Google Flow")
    parser.add_argument("--context", type=str, help="Optional scene context for ChatGPT consultation")
    parser.add_argument("-j", "--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    page_id = find_google_flow_page()

    if args.confirm_approve:
        res = handle_approval("approve", page_id=page_id)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"✅ Approved: {res}")
        return

    if args.confirm_reject:
        res = handle_approval("reject", page_id=page_id)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"🛑 Rejected: {res}")
        return

    # Check pending
    pending = detect_pending_approval(page_id=page_id)

    if args.evaluate_with_chatgpt:
        if not pending.get("hasPending"):
            out = {"hasPending": False, "message": "No pending approval active at this moment."}
            if args.json:
                print(json.dumps(out, indent=2))
            else:
                print("ℹ️ No active approval prompt is waiting in Google Flow.")
            return

        consultation = consult_chatgpt_for_approval(pending, story_context=args.context)
        out = {
            "pendingRequest": pending,
            "chatgptConsultation": consultation
        }
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print("=" * 75)
            print(" 🛎️ PENDING APPROVAL IN GOOGLE FLOW")
            print("=" * 75)
            print(f"Proposal   : {pending.get('requestText')}")
            print(f"Cost       : {pending.get('creditCost')} credits")
            print("-" * 75)
            print(f"🤖 ChatGPT Recommendation: {consultation['decision']}")
            print(f"💬 Reason: {consultation['reason']}")
            print("=" * 75)
            print("👉 To approve, run: python3 approval_consultant.py --confirm-approve")
            print("👉 To reject, run : python3 approval_consultant.py --confirm-reject")
        return

    # Default check status
    if args.json:
        print(json.dumps(pending, indent=2))
    else:
        print("=" * 75)
        print(" 🔍 APPROVAL STATUS")
        print("=" * 75)
        if pending.get("hasPending"):
            print("⚠️ PENDING APPROVAL DETECTED!")
            print(f"Proposal : {pending.get('requestText')}")
            print(f"Cost     : {pending.get('creditCost')} credits")
            print(f"Options  : {[c['text'] for c in pending.get('activeControls', [])]}")
        else:
            print("✅ No pending approval prompt currently waiting.")
        print("=" * 75)


if __name__ == "__main__":
    main()
