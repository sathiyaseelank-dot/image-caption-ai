#!/usr/bin/env python3
"""
Orca Smart DOM Pruner, Semantic Search & Verification Engine
------------------------------------------------------------
- Fast, lightweight, pure algorithmic processing on CPU.
- Page Describer: summarizes content, sections, and available inputs.
- Semantic Search: matches human intent to UI controls in <5ms.
- Ambiguity Protection: halts when multiple elements match equally.
- Change Verification & Error Detection: detects URL changes, validation
  failures, error banners, modal dialogs, and DOM text state updates post-action.
- Robust Error Handling: clear errors for missing binary, timeout, malformed JSON.
"""

import sys
import os
import json
import subprocess
import re
import time
import hashlib
from urllib.parse import urlparse, parse_qs
from typing import List, Dict, Any, Optional, Tuple

ORCA_BIN = os.environ.get("ORCA_BIN", "/home/inkyank-01/.config/orca/linux-orca-cli-shim/orca")

# Generic role/type keywords that should not by themselves trigger a substantive match
GENERIC_ROLE_TOKENS = {
    "button", "btn", "input", "field", "textbox", "box", "link",
    "combobox", "select", "dropdown", "textarea", "checkbox", "radio"
}

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
    "phone": ["telephone", "mobile", "cell", "contact"],
    "telephone": ["phone", "mobile", "cell"],
    "email": ["mail", "e-mail"],
    "name": ["customer", "user", "fullname", "person"],
    "comment": ["instruction", "instructions", "notes", "description", "details", "message"],
    "instruction": ["instructions", "comment", "notes", "details"],
    "notes": ["instruction", "instructions", "comment", "details"],
}

class OrcaError(Exception):
    """Base exception for Orca CLI operations."""
    pass

class OrcaNotFoundError(OrcaError):
    """Raised when the Orca executable is not found."""
    pass

class AmbiguousTargetError(OrcaError):
    """Raised when a semantic query matches multiple elements with similar confidence."""
    pass

class ElementNotFoundError(OrcaError):
    """Raised when no element matches the query."""
    pass

def run_cmd(args: List[str], timeout: float = 15.0) -> str:
    """Executes an Orca CLI command with proper error handling and timeout."""
    if not os.path.exists(ORCA_BIN):
        raise OrcaNotFoundError(
            f"Orca executable not found at '{ORCA_BIN}'. "
            f"Ensure Orca is installed or set the ORCA_BIN environment variable."
        )

    try:
        res = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise OrcaError(f"Orca command timed out after {timeout}s: {' '.join(args)}")
    except FileNotFoundError:
        raise OrcaNotFoundError(f"Failed to execute '{ORCA_BIN}': file not found.")

    if res.returncode != 0:
        err = res.stderr.strip() or res.stdout.strip()
        raise OrcaError(f"Command failed (exit code {res.returncode}): {err}")
    return res.stdout.strip()

def get_orca_snapshot() -> Dict[str, Any]:
    out = run_cmd([ORCA_BIN, "snapshot", "--json"])
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise OrcaError(f"Orca returned invalid JSON snapshot: {e}")

    if not isinstance(data, dict) or "result" not in data:
        raise OrcaError(f"Unexpected snapshot structure from Orca: missing 'result' object.")

    # Automatically enrich unlabelled inputs with surrounding DOM labels (e.g. table/bootstrap layouts)
    refs = data.get("result", {}).get("refs", {})
    unlabelled_refs = [
        ref_id for ref_id, info in refs.items()
        if info.get("role") in ["textbox", "combobox", "searchbox", "search"] and not info.get("name", "").strip()
    ]

    if unlabelled_refs:
        js = """
        (() => {
            const inputs = Array.from(document.querySelectorAll("input:not([type=hidden]), select, textarea"));
            return inputs.map((el, i) => {
                let label = "";
                if (el.labels && el.labels[0]) label = el.labels[0].innerText.trim();
                else if (el.getAttribute("aria-label")) label = el.getAttribute("aria-label").trim();
                else if (el.placeholder) label = el.placeholder.trim();
                else if (el.parentElement && el.parentElement.previousElementSibling) {
                    const prev = el.parentElement.previousElementSibling.innerText.trim();
                    if (prev.length < 50) label = prev;
                }
                else if (el.closest("td") && el.closest("td").previousElementSibling) {
                    const prev = el.closest("td").previousElementSibling.innerText.trim();
                    if (prev.length < 50) label = prev;
                }
                if (!label && el.name) label = el.name.replace(/^[0-9_]+/, "").replace(/_/g, " ").trim();

                // Qualify date dropdowns (Month / Day / Year)
                if (el.tagName === 'SELECT' && el.options && el.options.length > 0) {
                    const firstText = el.options[0].text.trim().toLowerCase();
                    if (['month', 'day', 'year', 'mm', 'dd', 'yy'].includes(firstText)) {
                        label = (label ? label + ' - ' : '') + el.options[0].text.trim();
                    } else if (el.name && (el.name.endsWith('mm') || el.name.includes('month'))) {
                        label = (label ? label + ' - ' : '') + 'Month';
                    } else if (el.name && (el.name.endsWith('dd') || el.name.includes('day'))) {
                        label = (label ? label + ' - ' : '') + 'Day';
                    } else if (el.name && (el.name.endsWith('yy') || el.name.includes('year'))) {
                        label = (label ? label + ' - ' : '') + 'Year';
                    }
                }

                return { index: i, resolvedLabel: label };
            });
        })()
        """
        try:
            dom_out = run_cmd([ORCA_BIN, "eval", "--expression", js])
            dom_labels = json.loads(dom_out)
            for i, ref_id in enumerate(unlabelled_refs):
                if i < len(dom_labels):
                    lbl = dom_labels[i].get("resolvedLabel")
                    if lbl:
                        refs[ref_id]["name"] = lbl
        except Exception:
            pass

    return data

def get_page_verification_state() -> Dict[str, Any]:
    """
    Captures deep page state for verification:
    - URL & Title
    - Invalid form fields (:invalid, aria-invalid)
    - Visible error messages & alerts
    - Open modal dialogs
    - Body text content hash
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
            '[role="alert"], [role="status"], [aria-live="assertive"], .alert, .error, .error-message, .invalid-feedback, .toast, .notice, .alert-banner'
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
            const isNativeDialogOpen = d.tagName.toLowerCase() === 'dialog' && d.open;
            const isVisibleAriaDialog = d.offsetParent !== null && window.getComputedStyle(d).display !== 'none';
            if (isNativeDialogOpen || isVisibleAriaDialog) {
                const titleEl = d.querySelector('h1, h2, h3, .modal-title, [id*="title"]');
                const title = titleEl ? titleEl.innerText.trim() : (d.id || 'Active Dialog');
                dialogs.push(title);
            }
        });

        // 4. Capture snapshot of all form input values
        const formValues = {};
        document.querySelectorAll('input:not([type=hidden]), select, textarea').forEach((el, idx) => {
            let label = '';
            if (el.labels && el.labels[0]) label = el.labels[0].innerText.trim();
            else if (el.parentElement && el.parentElement.previousElementSibling) label = el.parentElement.previousElementSibling.innerText.trim();
            const key = label || el.name || el.id || ('field_' + idx);
            const val = (el.type === 'checkbox' || el.type === 'radio') ? el.checked : el.value;
            formValues[key] = String(val);
        });

        const bodyTxt = document.body ? document.body.innerText.trim() : '';

        return {
            url: window.location.href,
            title: document.title || 'Untitled',
            bodyText: bodyTxt.slice(0, 5000),
            bodyLength: bodyTxt.length,
            formValues: formValues,
            invalidInputs: invalidInputs,
            alerts: alerts,
            dialogs: dialogs,
            error: null
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
            "bodyText": "",
            "bodyLength": 0,
            "invalidInputs": [],
            "alerts": [],
            "dialogs": [],
            "error": str(e)
        }

def is_error_alert(alert_text: str) -> bool:
    """Distinguishes between error/warning alerts and positive/success confirmation notices."""
    text_lower = alert_text.lower()
    positive_cues = ["success", "confirmed", "successfully", "saved", "completed", "submitted", "done", "scheduled", "booked"]
    error_cues = ["error", "fail", "invalid", "required", "warning", "denied", "reject", "prevented", "blocked", "disabled"]
    has_positive = any(c in text_lower for c in positive_cues)
    has_error = any(c in text_lower for c in error_cues)
    if has_positive and not has_error:
        return False
    return True

def compare_page_states(before: Dict[str, Any], after: Dict[str, Any], is_submit_action: bool = False) -> Dict[str, Any]:
    """Analyzes differences between before and after states to determine outcome."""
    if after.get("error"):
        return {
            "verdict": "INSPECTION_FAILED",
            "error": after["error"],
            "url_changed": False,
            "all_invalid_inputs": [],
            "new_invalid_inputs": [],
            "all_alerts": [],
            "new_alerts": [],
            "new_error_alerts": [],
            "new_success_alerts": [],
            "active_dialogs": []
        }

    url_changed = before.get("url") != after.get("url")
    title_changed = before.get("title") != after.get("title")

    # Detect new invalid inputs
    before_invalid_keys = {f"{inp.get('name')}:{inp.get('id')}:{inp.get('label')}" for inp in before.get("invalidInputs", [])}
    new_invalids = [
        inp for inp in after.get("invalidInputs", [])
        if f"{inp.get('name')}:{inp.get('id')}:{inp.get('label')}" not in before_invalid_keys
    ]
    all_current_invalids = after.get("invalidInputs", [])

    # Detect new alerts
    before_alerts = set(before.get("alerts", []))
    new_alerts = [a for a in after.get("alerts", []) if a not in before_alerts]
    new_error_alerts = [a for a in new_alerts if is_error_alert(a)]
    new_success_alerts = [a for a in new_alerts if not is_error_alert(a)]

    # Detect dialogs
    before_dialogs = before.get("dialogs", [])
    after_dialogs = after.get("dialogs", [])
    dialog_opened = len(after_dialogs) > len(before_dialogs)
    dialog_closed = len(after_dialogs) < len(before_dialogs)

    # Detect content text change
    content_changed = before.get("bodyText") != after.get("bodyText")

    # Detect form input values change (e.g. Reset button cleared fields or Autofill populated fields)
    before_forms = before.get("formValues", {})
    after_forms = after.get("formValues", {})
    all_keys = set(before_forms.keys()).union(after_forms.keys())
    changed_fields = [k for k in all_keys if before_forms.get(k) != after_forms.get(k)]
    form_values_changed = len(changed_fields) > 0

    # Determine verdict safely without false positives from pre-existing background errors
    if url_changed:
        verdict = "NAVIGATED"
    elif new_invalids or new_error_alerts:
        verdict = "VALIDATION_ERROR"
    elif is_submit_action and all_current_invalids and not url_changed:
        # User explicitly tried to submit, but form validation blocked navigation
        verdict = "VALIDATION_ERROR"
    elif new_success_alerts:
        verdict = "SUCCESS_NOTIFICATION"
    elif dialog_opened:
        verdict = "MODAL_OPENED"
    elif dialog_closed:
        verdict = "MODAL_CLOSED"
    elif form_values_changed:
        verdict = "FORM_UPDATED"
    elif content_changed:
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
        "new_error_alerts": new_error_alerts,
        "new_success_alerts": new_success_alerts,
        "all_alerts": after.get("alerts", []),
        "dialog_opened": dialog_opened,
        "dialog_closed": dialog_closed,
        "active_dialogs": after_dialogs,
        "content_changed": content_changed,
        "form_values_changed": form_values_changed,
        "changed_fields": changed_fields
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
    elif verdict == "SUCCESS_NOTIFICATION":
        print("✅ STATUS: SUCCESS NOTIFICATION / ACTION CONFIRMED")
        for a in res.get("new_success_alerts", []):
            print(f"   🎉 \"{a}\"")
    elif verdict == "VALIDATION_ERROR":
        print("❌ STATUS: FORM VALIDATION / SUBMISSION FAILED")
        invalids = res["new_invalid_inputs"] if res["new_invalid_inputs"] else res["all_invalid_inputs"]
        if invalids:
            print("\n   ⚠️  Invalid Fields Detected:")
            for inp in invalids:
                name = inp["label"] or inp["name"] or inp["id"]
                msg = inp["validationMessage"]
                print(f"      • Field: \"{name}\" ({inp['tag']}) ➔ Error: \"{msg}\"")
        alerts = res.get("new_error_alerts") or res["new_alerts"] or res["all_alerts"]
        if alerts:
            print("\n   🚨 Alert / Error Banners:")
            for a in alerts:
                print(f"      • \"{a}\"")
    elif verdict == "MODAL_OPENED":
        print("ℹ️  STATUS: MODAL / DIALOG OPENED")
        print(f"   Active Dialogs: {', '.join(res['active_dialogs'])}")
    elif verdict == "MODAL_CLOSED":
        print("ℹ️  STATUS: MODAL / DIALOG CLOSED")
    elif verdict == "FORM_UPDATED":
        print("✅ STATUS: FORM VALUES UPDATED (Fields cleared / modified)")
        if res.get("changed_fields"):
            print(f"   Modified Fields: {', '.join(res['changed_fields'][:6])}")
    elif verdict == "CONTENT_UPDATED":
        print("✅ STATUS: IN-PAGE CONTENT UPDATED (DOM state changed)")
    elif verdict == "INSPECTION_FAILED":
        print(f"❌ STATUS: INSPECTION FAILED: {res.get('error')}")
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
    """
    Computes hybrid BM25 + Intent similarity score.
    Includes safeguard: generic role tokens alone cannot match an element
    if substantive descriptive query tokens were provided.
    """
    q_tokens = tokenize(query)
    doc_tokens = tokenize(element["semantic_text"])
    if not q_tokens or not doc_tokens:
        return 0.0

    score = 0.0
    doc_text = " ".join(doc_tokens)
    name_text = element["name"].lower()
    name_tokens = tokenize(name_text)
    role_text = element["role"].lower()

    # Distinguish between substantive query words and generic role keywords
    substantive_q_tokens = [t for t in q_tokens if t not in GENERIC_ROLE_TOKENS]
    has_substantive = len(substantive_q_tokens) > 0

    substantive_matched = False

    # Exact full match bonus
    if query.strip().lower() == name_text:
        score += 5.0
        substantive_matched = True
    elif query.strip().lower() in name_text:
        score += 3.0
        substantive_matched = True

    for token in q_tokens:
        is_substantive = token not in GENERIC_ROLE_TOKENS

        if token in doc_tokens:
            score += 1.5
            if token in name_tokens:
                score += 1.0
                if is_substantive:
                    substantive_matched = True
            if token == role_text:
                # Structural role match bonus
                score += 0.8
        else:
            # Synonym lookup
            for base_word, synonyms in INTENT_SYNONYMS.items():
                if token == base_word or token in synonyms:
                    matched = [s for s in ([base_word] + synonyms) if s in name_tokens]
                    if matched:
                        score += 1.2
                        if is_substantive:
                            substantive_matched = True

    # If query had descriptive substantive tokens but NONE matched the element,
    # reject match so generic role words (like 'button') don't falsely trigger
    if has_substantive and not substantive_matched:
        return 0.0

    if element["is_interactive"]:
        score *= 1.25

    length_penalty = 1.0 / (1.0 + 0.01 * max(0, len(doc_tokens) - len(q_tokens)))
    score *= length_penalty
    return round(score, 3)

def resolve_target_element(target: str, confidence_threshold: float = 1.0) -> Tuple[str, Dict[str, Any]]:
    """
    Resolves target query to element ref.
    Guards against ambiguity when multiple elements have similar high scores.
    """
    clean = target.strip().lstrip("@")
    snapshot = get_orca_snapshot()
    refs = snapshot.get("result", {}).get("refs", {})

    # Direct ref check (e.g. 'e3')
    if clean in refs:
        return clean, refs[clean]

    pruned = smart_dom_prune(snapshot)
    scored = []
    for el in pruned:
        score = compute_semantic_score(target, el)
        if score >= confidence_threshold:
            scored.append((score, el))

    if not scored:
        raise ElementNotFoundError(f"Could not find any element matching query: '{target}'")

    scored.sort(key=lambda x: x[0], reverse=True)

    # Ambiguity check: if top 2 candidates have distinct names and near-identical scores
    if len(scored) >= 2:
        top1_score, top1_el = scored[0]
        top2_score, top2_el = scored[1]
        score_diff = abs(top1_score - top2_score)

        if score_diff < 0.15 and top1_el["name"].lower() != top2_el["name"].lower():
            candidates = [f"[{el['ref']}] \"{el['name']}\" ({el['role']}) (score: {s})" for s, el in scored[:3] if abs(s - top1_score) < 0.15]
            raise AmbiguousTargetError(
                f"Query '{target}' is ambiguous between multiple elements:\n  • " +
                "\n  • ".join(candidates) +
                "\nPlease provide a more specific label or use the element ref directly."
            )

    best = scored[0][1]
    return best["ref"], best

def act_click_and_verify(target: str, wait_seconds: float = 1.0) -> Dict[str, Any]:
    print(f"\n🎯 Finding target element for click: '{target}'...")
    ref, info = resolve_target_element(target)
    name = info.get("name") or info.get("role")
    role = info.get("role", "")
    print(f"👉 Matched element: [{ref}] \"{name}\" ({role})")

    # Check if this is a submission action
    is_submit = "submit" in target.lower() or "submit" in name.lower() or "save" in target.lower()

    # Auto-scroll element into view so clicks never miss off-screen elements
    scroll_js = f"""
    (() => {{
        const target = {json.dumps(name)};
        const candidates = Array.from(document.querySelectorAll('button, input, a, select, textarea, [role=button], [role=combobox]'));
        const match = candidates.find(el => {{
            const txt = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim().toLowerCase();
            return txt === target.toLowerCase() || txt.includes(target.toLowerCase());
        }});
        if (match) {{
            match.scrollIntoView({{block: 'center', inline: 'center'}});
            return true;
        }}
        return false;
    }})()
    """
    try:
        run_cmd([ORCA_BIN, "eval", "--expression", scroll_js])
        time.sleep(0.2)
    except Exception:
        pass

    before_state = get_page_verification_state()
    print(f"⚡ Executing: orca click --element {ref}")
    run_cmd([ORCA_BIN, "click", "--element", ref])

    if wait_seconds > 0:
        time.sleep(wait_seconds)

    after_state = get_page_verification_state()
    comparison = compare_page_states(before_state, after_state, is_submit_action=is_submit)
    print_verification_report(comparison)
    return comparison

def act_fill_and_verify(target: str, value: str) -> Dict[str, Any]:
    print(f"\n🎯 Finding target field for fill: '{target}'...")
    ref, info = resolve_target_element(target)
    name = info.get("name") or info.get("role")
    role = info.get("role", "")
    print(f"👉 Matched input field: [{ref}] \"{name}\" ({role})")

    # If target is a dropdown / combobox / select, handle text-to-value resolution
    select_js = rf"""
    (() => {{
        const targetName = {json.dumps(name)};
        const desiredVal = {json.dumps(value)};
        const target = targetName.toLowerCase();
        const desired = desiredVal.toLowerCase();
        const words = target.split(/[\s\-_]+/).filter(Boolean);
        const selects = Array.from(document.querySelectorAll('select'));
        
        let bestSelect = null;
        let bestScore = -1;
        
        selects.forEach(s => {{
            let l = '';
            if (s.labels && s.labels[0]) l = s.labels[0].innerText;
            else if (s.parentElement && s.parentElement.previousElementSibling) l = s.parentElement.previousElementSibling.innerText;
            const text = (l + ' ' + s.name + ' ' + (s.options[0]?.text || '')).toLowerCase();
            let score = 0;
            words.forEach(w => {{ if (text.includes(w)) score++; }});
            if (score > bestScore) {{
                bestScore = score;
                bestSelect = s;
            }}
        }});

        if (bestSelect) {{
            bestSelect.scrollIntoView({{block: 'center'}});
            const opt = Array.from(bestSelect.options).find(o => 
                o.text.trim().toLowerCase() === desired ||
                o.text.trim().toLowerCase().startsWith(desired) ||
                o.value.trim().toLowerCase() === desired
            );
            if (opt) {{
                bestSelect.value = opt.value;
                bestSelect.dispatchEvent(new Event('change', {{bubbles: true}}));
                return 'select_set:' + opt.text;
            }}
        }}
        return 'standard';
    }})()
    """
    try:
        res = run_cmd([ORCA_BIN, "eval", "--expression", select_js])
        if "select_set:" in res:
            selected_text = res.split("select_set:")[1]
            print(f"⚡ Selected dropdown option: \"{selected_text}\"")
        else:
            print(f"⚡ Executing: orca fill --element {ref} --value \"{value}\"")
            run_cmd([ORCA_BIN, "fill", "--element", ref, "--value", value])
    except Exception:
        print(f"⚡ Executing: orca fill --element {ref} --value \"{value}\"")
        run_cmd([ORCA_BIN, "fill", "--element", ref, "--value", value])

    before_state = get_page_verification_state()
    time.sleep(0.3)
    after_state = get_page_verification_state()
    comparison = compare_page_states(before_state, after_state)

    print("\n" + "=" * 70)
    print(" 🔎 INPUT VERIFICATION REPORT")
    print("=" * 70)
    print(f"✅ Filled element [{ref}] with: \"{value}\"")
    if comparison.get("all_invalid_inputs"):
        matching_invalid = [i for i in comparison["all_invalid_inputs"] if i.get("label") == name or i.get("id") == ref]
        if matching_invalid:
            print(f"⚠️  Field has validation warning: {matching_invalid[0].get('validationMessage')}")
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

    if state.get("error"):
        print(f"❌ Error inspecting page: {state['error']}")
        print("   Could not retrieve live DOM validity status.")
        print("=" * 70)
        sys.exit(1)

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

    # Improved page categorization
    parsed_url = urlparse(state["url"])
    is_search_page = (
        parsed_url.path.startswith("/search")
        or "q=" in parsed_url.query
        or "query=" in parsed_url.query
        or "google.com/search" in state["url"]
    )

    if is_search_page:
        page_type = "Search Results / Search Engine Page"
    elif len(inputs) >= 2 or any("form" in state["url"].lower() for _ in [1]):
        page_type = "Interactive Form / Data-Entry Page"
    else:
        page_type = "Content / Information Page"

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
            scored.append((score, el))

    scored.sort(key=lambda x: x[0], reverse=True)

    print(f"\n🔍 Querying Orca Active Page for: '{query}'...")
    print(f"🌐 Page Origin: {origin}")
    print(f"📊 DOM Pruning: Reduced from {total_refs} raw refs to {len(pruned)} clean semantic nodes.")
    print("=" * 70)

    if not scored:
        print("❌ No matching elements found.")
        return

    # Check ambiguity among top candidates
    if len(scored) >= 2:
        diff = abs(scored[0][0] - scored[1][0])
        if diff < 0.15 and scored[0][1]["name"].lower() != scored[1][1]["name"].lower():
            print(f"⚠️  NOTE: Ambiguous match detected between multiple elements:")
            for s, el in scored[:2]:
                print(f"   • [{el['ref']}] \"{el['name']}\" (Score: {s})")
            print("-" * 70)

    for i, (score, el) in enumerate(scored[:top_k], 1):
        ref = el["ref"]
        role = el["role"]
        name = el["name"] if len(el["name"]) <= 60 else el["name"][:57] + "..."
        action = el["suggested_action"]

        print(f"#{i} [{ref}] (Score: {score})")
        print(f"   Role: {role:<12} | Name: \"{name}\"")
        if action == "fill":
            print(f"   👉 One-Step Fill : python3 orca_smart_search.py --fill \"{el['name']}\" \"<text>\"")
        else:
            print(f"   👉 One-Step Click: python3 orca_smart_search.py --click \"{el['name']}\"")
        print("-" * 70)

def main():
    try:
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
    except AmbiguousTargetError as e:
        print(f"\n⚠️  AMBIGUITY ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    except ElementNotFoundError as e:
        print(f"\n❌ NOT FOUND: {e}", file=sys.stderr)
        sys.exit(3)
    except OrcaNotFoundError as e:
        print(f"\n❌ ORCA CLI ERROR: {e}", file=sys.stderr)
        sys.exit(4)
    except OrcaError as e:
        print(f"\n❌ ORCA ERROR: {e}", file=sys.stderr)
        sys.exit(5)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
