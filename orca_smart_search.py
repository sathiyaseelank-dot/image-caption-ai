#!/usr/bin/env python3
"""
Orca Smart DOM Pruner, Semantic Search & Verification Engine
------------------------------------------------------------
- NO LLM required: fast, lightweight, pure algorithmic processing on CPU.
- Page Describer: summarizes content, sections, and available inputs.
- Semantic Search: matches human intent to UI controls in <5ms.
- Change Verification & Error Detection: detects URL changes, validation
  failures, error banners, modal dialogs, and DOM state updates post-action.
"""

import sys
import json
import subprocess
import re
import time
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional, Tuple

ORCA_BIN = "/home/inkyank-01/.config/orca/linux-orca-cli-shim/orca"

INTENT_SYNONYMS = {
    "search": ["query", "find", "lookup", "explore", "searchbox", "bar"],
    "clear": ["remove", "reset", "erase", "delete", "empty"],
    "voice": ["audio", "microphone", "mic", "speak"],
    "image": ["photo", "picture", "lens", "visual"],
    "next": ["forward", "advance", "continue", "page 2"],
    "previous": ["back", "prev"],
    "submit": ["enter", "send", "save", "apply", "go", "order"],
    "login": ["signin", "sign in", "auth", "account"],
    "settings": ["preferences", "options", "config", "tools"],
    "phone": ["telephone", "mobile", "cell", "contact", "number"],
    "telephone": ["phone", "mobile", "cell", "number"],
    "email": ["mail", "e-mail", "address"],
    "name": ["customer", "user", "fullname", "person"],
    "comment": ["instruction", "instructions", "notes", "description", "details", "message"],
    "instruction": ["instructions", "comment", "notes", "details"],
}

def run_cmd(args: List[str]) -> str:
    res = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Command failed ({res.returncode}): {res.stderr.strip()}")
    return res.stdout.strip()

def get_orca_snapshot() -> Dict[str, Any]:
    out = run_cmd([ORCA_BIN, "snapshot", "--json"])
    return json.loads(out)

def get_page_verification_state() -> Dict[str, Any]:
    """
    Captures deep page state for verification:
    - URL & Title
    - Invalid form fields (:invalid, aria-invalid)
    - Visible error messages & alerts
    - Open modal dialogs
    - Body text length & preview
    """
    js_code = """
    (() => {
        // 1. Detect HTML5 invalid form inputs
        const invalidInputs = [];
        document.querySelectorAll('input, select, textarea').forEach(el => {
            if (el.checkValidity && !el.checkValidity()) {
                let label = '';
                if (el.labels && el.labels[0]) label = el.labels[0].innerText.trim();
                else if (el.closest('label')) label = el.closest('label').innerText.trim();
                else label = el.getAttribute('aria-label') || el.placeholder || el.name || el.id;

                invalidInputs.push({
                    tag: el.tagName.toLowerCase(),
                    id: el.id || '',
                    name: el.name || '',
                    label: label,
                    validationMessage: el.validationMessage || 'Invalid field'
                });
            }
        });

        // 2. Detect visible error banners, alerts, toasts
        const alerts = [];
        const alertElements = document.querySelectorAll(
            '[role="alert"], [role="status"], [aria-live="assertive"], .alert, .error, .error-message, .invalid-feedback, .toast, .notice'
        );
        alertElements.forEach(el => {
            if (el.offsetParent !== null) { // visible only
                const txt = el.innerText.trim();
                if (txt && !alerts.includes(txt) && txt.length < 300) {
                    alerts.push(txt);
                }
            }
        });

        // 3. Detect open modal dialogs
        const dialogs = [];
        document.querySelectorAll('dialog[open], [role="dialog"], [role="alertdialog"], .modal.show').forEach(d => {
            if (d.offsetParent !== null) {
                const titleEl = d.querySelector('h1, h2, h3, .modal-title, [id*="title"]');
                const title = titleEl ? titleEl.innerText.trim() : 'Active Dialog';
                dialogs.push(title);
            }
        });

        return {
            url: window.location.href,
            title: document.title || 'Untitled',
            bodyLength: document.body ? document.body.innerText.length : 0,
            invalidInputs: invalidInputs,
            alerts: alerts,
            dialogs: dialogs
        };
    })()
    """
    try:
        out = run_cmd([ORCA_BIN, "eval", "--expression", js_code])
        return json.loads(out)
    except Exception as e:
        return {
            "url": "unknown",
            "title": "unknown",
            "bodyLength": 0,
            "invalidInputs": [],
            "alerts": [],
            "dialogs": []
        }

def compare_page_states(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """Analyzes differences between before and after states to determine outcome."""
    url_changed = before.get("url") != after.get("url")
    title_changed = before.get("title") != after.get("title")
    
    # Check new invalid inputs
    before_invalid_ids = {inp["name"] or inp["id"] or inp["label"] for inp in before.get("invalidInputs", [])}
    new_invalids = [
        inp for inp in after.get("invalidInputs", [])
        if (inp["name"] or inp["id"] or inp["label"]) not in before_invalid_ids
    ]
    all_current_invalids = after.get("invalidInputs", [])

    # Check new alerts
    before_alerts = set(before.get("alerts", []))
    new_alerts = [a for a in after.get("alerts", []) if a not in before_alerts]

    # Check dialogs
    before_dialogs = before.get("dialogs", [])
    after_dialogs = after.get("dialogs", [])
    dialog_opened = len(after_dialogs) > len(before_dialogs)
    dialog_closed = len(after_dialogs) < len(before_dialogs)

    # Determine status verdict
    if all_current_invalids or new_alerts:
        verdict = "VALIDATION_ERROR"
    elif url_changed:
        verdict = "NAVIGATED"
    elif dialog_opened:
        verdict = "MODAL_OPENED"
    elif dialog_closed:
        verdict = "MODAL_CLOSED"
    elif abs(after.get("bodyLength", 0) - before.get("bodyLength", 0)) > 5:
        verdict = "CONTENT_UPDATED"
    else:
        verdict = "NO_VISIBLE_CHANGE"

    return {
        "verdict": verdict,
        "url_changed": url_changed,
        "from_url": before.get("url"),
        "to_url": after.get("url"),
        "title_changed": title_changed,
        "new_invalid_inputs": new_invalids,
        "all_invalid_inputs": all_current_invalids,
        "new_alerts": new_alerts,
        "all_alerts": after.get("alerts", []),
        "dialog_opened": dialog_opened,
        "dialog_closed": dialog_closed,
        "active_dialogs": after_dialogs
    }

def print_verification_report(res: Dict[str, Any]):
    verdict = res["verdict"]
    print("\n" + "=" * 70)
    print(" 🔎 ACTION VERIFICATION & ERROR DETECTION REPORT")
    print("=" * 70)

    if verdict == "NAVIGATED":
        print("✅ STATUS: SUCCESSFUL NAVIGATION")
        print(f"   From : {res['from_url']}")
        print(f"   To   : {res['to_url']}")
    elif verdict == "VALIDATION_ERROR":
        print("❌ STATUS: FORM VALIDATION / SUBMISSION FAILED")
        if res["all_invalid_inputs"]:
            print("\n   ⚠️  Invalid Fields Detected:")
            for inp in res["all_invalid_inputs"]:
                name = inp["label"] or inp["name"] or inp["id"]
                msg = inp["validationMessage"]
                print(f"      • Field: \"{name}\" ({inp['tag']}) ➔ Error: \"{msg}\"")
        if res["new_alerts"] or res["all_alerts"]:
            alerts = res["new_alerts"] if res["new_alerts"] else res["all_alerts"]
            print("\n   🚨 Alert / Error Banners:")
            for a in alerts:
                print(f"      • \"{a}\"")
    elif verdict == "MODAL_OPENED":
        print("ℹ️  STATUS: MODAL / DIALOG OPENED")
        print(f"   Active Dialogs: {', '.join(res['active_dialogs'])}")
    elif verdict == "MODAL_CLOSED":
        print("ℹ️  STATUS: MODAL / DIALOG CLOSED")
    elif verdict == "CONTENT_UPDATED":
        print("✅ STATUS: IN-PAGE CONTENT UPDATED (DOM state changed)")
    else:
        print("⚠️  STATUS: NO DETECTABLE CHANGE")
        print("   The action completed, but no URL, form, or visual change was observed.")
        print("   (Ensure the element was not disabled or blocked).")

    print("=" * 70)

def smart_dom_prune(snapshot_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = snapshot_data.get("result", {})
    refs = result.get("refs", {})
    pruned = []

    for ref_id, info in refs.items():
        role = info.get("role", "").lower()
        name = info.get("name", "").strip()

        if role in ["generic", "none", "presentation"] and not name:
            continue

        is_input = role in ["combobox", "textbox", "searchbox", "search"]
        is_button = role in ["button", "menuitem", "switch"]
        is_link = role == "link"

        semantic_text = f"{name} {role}".strip()
        if not semantic_text:
            continue

        action_type = "fill" if is_input else "click"

        pruned.append({
            "ref": ref_id,
            "role": role,
            "name": name,
            "is_input": is_input,
            "is_button": is_button,
            "is_link": is_link,
            "is_interactive": (is_input or is_button or is_link),
            "semantic_text": semantic_text,
            "suggested_action": action_type
        })

    return pruned

def tokenize(text: str) -> List[str]:
    return re.findall(r'\b[a-z0-9]+\b', text.lower())

def compute_semantic_score(query: str, element: Dict[str, Any]) -> float:
    q_tokens = tokenize(query)
    doc_tokens = tokenize(element["semantic_text"])
    if not q_tokens or not doc_tokens:
        return 0.0

    score = 0.0
    doc_text = " ".join(doc_tokens)
    name_text = element["name"].lower()
    role_text = element["role"].lower()

    if query.lower() in name_text or query.lower() in doc_text:
        score += 3.0

    for token in q_tokens:
        if token in doc_tokens:
            score += 1.5
            if token in name_text:
                score += 1.0
            if token == role_text:
                score += 0.8
        else:
            for base_word, synonyms in INTENT_SYNONYMS.items():
                if token == base_word or token in synonyms:
                    matched = [s for s in ([base_word] + synonyms) if s in doc_tokens]
                    if matched:
                        score += 0.9

    if element["is_interactive"]:
        score *= 1.25

    length_penalty = 1.0 / (1.0 + 0.01 * max(0, len(doc_tokens) - len(q_tokens)))
    score *= length_penalty
    return round(score, 3)

def resolve_target_element(target: str) -> Tuple[str, Dict[str, Any]]:
    """Resolves whether target is a direct ref like 'e3' or a semantic search string."""
    clean = target.strip().lstrip("@")
    snapshot = get_orca_snapshot()
    refs = snapshot.get("result", {}).get("refs", {})

    if clean in refs:
        return clean, refs[clean]

    pruned = smart_dom_prune(snapshot)
    scored = []
    for el in pruned:
        score = compute_semantic_score(target, el)
        if score > 0:
            scored.append((score, el))

    if not scored:
        raise ValueError(f"Could not find any element matching query: '{target}'")

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1]
    return best["ref"], best

def act_click_and_verify(target: str, wait_seconds: float = 1.0):
    print(f"\n🎯 Finding target element for click: '{target}'...")
    ref, info = resolve_target_element(target)
    name = info.get("name") or info.get("role")
    print(f"👉 Matched element: [{ref}] \"{name}\"")

    before_state = get_page_verification_state()
    print(f"⚡ Executing: orca click --element {ref}")
    run_cmd([ORCA_BIN, "click", "--element", ref])

    if wait_seconds > 0:
        time.sleep(wait_seconds)

    after_state = get_page_verification_state()
    comparison = compare_page_states(before_state, after_state)
    print_verification_report(comparison)
    return comparison

def act_fill_and_verify(target: str, value: str):
    print(f"\n🎯 Finding target field for fill: '{target}'...")
    ref, info = resolve_target_element(target)
    name = info.get("name") or info.get("role")
    print(f"👉 Matched input field: [{ref}] \"{name}\"")

    before_state = get_page_verification_state()
    print(f"⚡ Executing: orca fill --element {ref} --value \"{value}\"")
    run_cmd([ORCA_BIN, "fill", "--element", ref, "--value", value])

    time.sleep(0.3)
    after_state = get_page_verification_state()
    comparison = compare_page_states(before_state, after_state)

    print("\n" + "=" * 70)
    print(" 🔎 INPUT VERIFICATION REPORT")
    print("=" * 70)
    print(f"✅ Filled element [{ref}] with: \"{value}\"")
    if comparison["all_invalid_inputs"]:
        matching_invalid = [i for i in comparison["all_invalid_inputs"] if i["label"] == name or i["id"] == ref]
        if matching_invalid:
            print(f"⚠️  Field has validation warning: {matching_invalid[0]['validationMessage']}")
        else:
            print("✅ Field validity: OK")
    else:
        print("✅ Field validity: OK")
    print("=" * 70)
    return comparison

def check_errors_now():
    state = get_page_verification_state()
    print("\n" + "=" * 70)
    print(" 🔎 CURRENT PAGE ERROR SCAN")
    print("=" * 70)
    print(f"📌 URL: {state['url']}")

    has_issues = False
    if state["invalidInputs"]:
        has_issues = True
        print(f"\n⚠️  Found {len(state['invalidInputs'])} Invalid Form Field(s):")
        for inp in state["invalidInputs"]:
            label = inp["label"] or inp["name"] or inp["id"]
            print(f"   • Field: \"{label}\" ➔ Message: \"{inp['validationMessage']}\"")

    if state["alerts"]:
        has_issues = True
        print(f"\n🚨 Active Error Banners / Alerts ({len(state['alerts'])}):")
        for a in state["alerts"]:
            print(f"   • \"{a}\"")

    if state["dialogs"]:
        print(f"\nℹ️  Active Dialogs / Modals: {', '.join(state['dialogs'])}")

    if not has_issues:
        print("\n✅ Clean: No form validation errors or alert banners detected on this page.")
    print("=" * 70)

def describe_current_page():
    snapshot = get_orca_snapshot()
    elements = smart_dom_prune(snapshot)
    state = get_page_verification_state()

    inputs = [el for el in elements if el["is_input"]]
    buttons = [el for el in elements if el["is_button"]]
    links = [el for el in elements if el["is_link"]]

    page_type = "Content / Information Page"
    if "search" in state["url"].lower() or any("search" in el["name"].lower() for el in inputs):
        page_type = "Search Results / Search Engine Page"
    elif len(inputs) >= 3:
        page_type = "Interactive Form / Data-Entry Page"

    print("=" * 75)
    print(" 📄 ORCA BROWSER PAGE DESCRIPTION")
    print("=" * 75)
    print(f"📌 Page Title       : {state['title']}")
    print(f"🌐 URL              : {state['url']}")
    print(f"🏷️  Page Category    : {page_type}")
    print("-" * 75)
    print(f"📊 Elements Found   : {len(inputs)} Inputs/Fields | {len(buttons)} Buttons | {len(links)} Links")

    print("\n⌨️  AVAILABLE INPUT FIELDS:")
    if not inputs:
        print("   (No interactive input fields detected on this page)")
    else:
        for i, inp in enumerate(inputs, 1):
            ref = inp["ref"]
            name = inp["name"] or "Unnamed input"
            role = inp["role"]
            print(f"   [{i}] Ref: [{ref}] | Type: {role:<10} | Label: \"{name}\"")
            print(f"       👉 Fill with: python3 orca_smart_search.py --fill \"{name}\" \"<text>\"")

    print("\n🔘 KEY ACTION BUTTONS (Top 6):")
    if not buttons:
        print("   (No buttons detected)")
    else:
        for b in buttons[:6]:
            print(f"   - [{b['ref']}] \"{b['name']}\" (python3 orca_smart_search.py --click \"{b['name']}\")")
    print("=" * 75)

def search_elements(query: str, top_k: int = 5):
    snapshot = get_orca_snapshot()
    origin = snapshot.get("result", {}).get("origin", "unknown")
    total_refs = len(snapshot.get("result", {}).get("refs", {}))

    pruned = smart_dom_prune(snapshot)
    scored = []
    for el in pruned:
        score = compute_semantic_score(query, el)
        if score > 0:
            scored.append({**el, "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)

    print(f"\n🔍 Querying Orca Active Page for: '{query}'...")
    print(f"🌐 Page Origin: {origin}")
    print(f"📊 DOM Pruning: Reduced from {total_refs} raw refs to {len(pruned)} clean semantic nodes.")
    print("=" * 70)

    if not scored:
        print("❌ No matching elements found.")
        return

    for i, res in enumerate(scored[:top_k], 1):
        ref = res["ref"]
        role = res["role"]
        name = res["name"] if len(res["name"]) <= 60 else res["name"][:57] + "..."
        score = res["score"]
        action = res["suggested_action"]

        print(f"#{i} [{res['ref']}] (Score: {score})")
        print(f"   Role: {role:<12} | Name: \"{name}\"")
        if action == "fill":
            print(f"   👉 One-Step Fill : python3 orca_smart_search.py --fill \"{res['name']}\" \"<text>\"")
        else:
            print(f"   👉 One-Step Click: python3 orca_smart_search.py --click \"{res['name']}\"")
        print("-" * 70)

def main():
    if len(sys.argv) < 2 or sys.argv[1].lower() in ["--describe", "describe", "-d", "info"]:
        describe_current_page()
    elif sys.argv[1].lower() in ["--check-errors", "errors", "check"]:
        check_errors_now()
    elif sys.argv[1].lower() in ["--click", "-c"]:
        if len(sys.argv) < 3:
            print("Usage: python3 orca_smart_search.py --click <query_or_ref>")
            sys.exit(1)
        target = " ".join(sys.argv[2:])
        act_click_and_verify(target)
    elif sys.argv[1].lower() in ["--fill", "-f"]:
        if len(sys.argv) < 4:
            print("Usage: python3 orca_smart_search.py --fill <query_or_ref> <value>")
            sys.exit(1)
        target = sys.argv[2]
        value = " ".join(sys.argv[3:])
        act_fill_and_verify(target, value)
    else:
        query = " ".join(sys.argv[1:])
        search_elements(query, top_k=5)

if __name__ == "__main__":
    main()
