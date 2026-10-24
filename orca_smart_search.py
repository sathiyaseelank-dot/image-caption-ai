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
import base64
import datetime
import builtins
from urllib.parse import urlparse, parse_qs
from typing import List, Dict, Any, Optional, Tuple

_builtin_print = builtins.print
IS_JSON_MODE = False

def print_log(*args, **kwargs):
    if not IS_JSON_MODE:
        _builtin_print(*args, **kwargs)

print = print_log

def emit_json(data: Dict[str, Any]):
    _builtin_print(json.dumps(data, indent=2))

ORCA_BIN = os.environ.get("ORCA_BIN", "/home/inkyank-01/.config/orca/linux-orca-cli-shim/orca")
# Optional stable browser page id (from `orca tab list --json`) to pin every command to one tab
# instead of whichever tab happens to be active.
ORCA_PAGE = os.environ.get("ORCA_PAGE", "").strip()

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
    def __init__(self, message: str, candidates: Optional[List[Any]] = None):
        super().__init__(message)
        self.candidates = candidates or []

class ElementNotFoundError(OrcaError):
    """Raised when no element matches the query."""
    def __init__(self, message: str, target: Optional[str] = None):
        super().__init__(message)
        self.target = target

class OptionUnavailableError(OrcaError):
    """Raised when a requested option is not available in a dropdown / select element."""
    def __init__(self, message: str, available_options: Optional[List[str]] = None, target: Optional[str] = None, element: Optional[str] = None):
        super().__init__(message)
        self.available_options = available_options or []
        self.target = target
        self.element = element

class ElementDisabledError(OrcaError):
    """Raised when attempting to interact with a disabled element."""
    def __init__(self, message: str, target: Optional[str] = None):
        super().__init__(message)
        self.target = target

def run_cmd(args: List[str], timeout: float = 15.0) -> str:
    """Executes an Orca CLI command with proper error handling and timeout."""
    if not os.path.exists(ORCA_BIN):
        raise OrcaNotFoundError(
            f"Orca executable not found at '{ORCA_BIN}'. "
            f"Ensure Orca is installed or set the ORCA_BIN environment variable."
        )

    if ORCA_PAGE and args and args[0] == ORCA_BIN and "--page" not in args:
        args = args + ["--page", ORCA_PAGE]

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

def run_orca_json(args: List[str], timeout: float = 15.0) -> Dict[str, Any]:
    """Runs a ref-based Orca command in --json mode and returns its 'result' object."""
    out = run_cmd([ORCA_BIN] + args + ["--json"], timeout=timeout)
    try:
        data = json.loads(out)
    except (json.JSONDecodeError, TypeError) as e:
        raise OrcaError(f"Orca returned invalid JSON for '{args[0]}': {e}")
    if not isinstance(data, dict):
        raise OrcaError(f"Unexpected response from Orca for '{args[0]}'.")
    if data.get("ok") is False:
        raise OrcaError(f"Orca '{args[0]}' failed: {data.get('error') or data}")
    result = data.get("result")
    return result if isinstance(result, dict) else {}

# Blurs whatever currently has focus (following focus into iframes and shadow roots),
# so a focus call that silently fails can never leave us acting on the previous field.
BLUR_ACTIVE_JS = """
(() => {
    let el = document.activeElement;
    let deepest = el;
    while (el) {
        deepest = el;
        let next = null;
        if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') {
            try { next = el.contentDocument && el.contentDocument.activeElement; } catch (e) {}
        } else if (el.shadowRoot) {
            next = el.shadowRoot.activeElement;
        }
        if (!next || next === el) break;
        el = next;
    }
    if (deepest && deepest.blur) deepest.blur();
    return true;
})()
"""

# Inspects (and optionally sets the value of) the element that currently has focus.
# Combined with `orca focus --element <ref>`, this addresses the exact element behind a
# snapshot ref on any site, including same-origin iframes and open shadow roots.
# The element is remembered under a per-action key, so later steps of the same action keep
# addressing it even if its ref goes stale (e.g. widgets that re-render after a value change).
BOUND_ELEMENT_JS = """
(() => {
    const OP = __OP__;
    const VALUE = __VALUE__;
    const MODE = __MODE__;
    const KEY = __KEY__;
    const store = window.__orcaSmartBound = window.__orcaSmartBound || {};
    let el = null;
    if (MODE === 'stored') {
        el = store[KEY];
        if (!el || !el.isConnected) return JSON.stringify({ error: 'no-stored-element' });
    } else {
        el = document.activeElement;
        while (el) {
            if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') {
                let inner = null;
                try { inner = el.contentDocument && el.contentDocument.activeElement; }
                catch (e) { return JSON.stringify({ error: 'element is inside a cross-origin frame' }); }
                if (!inner || inner === el) break;
                el = inner;
            } else if (el.shadowRoot && el.shadowRoot.activeElement) {
                el = el.shadowRoot.activeElement;
            } else {
                break;
            }
        }
        const doc = el ? el.ownerDocument : null;
        if (!el || el === doc.body || el === doc.documentElement || el.tagName === 'IFRAME' || el.tagName === 'FRAME') {
            return JSON.stringify({ error: 'element did not receive focus' });
        }
        if (KEY) store[KEY] = el;
    }
    const tag = el.tagName.toLowerCase();

    if (OP === 'set_value') {
        if (el.isContentEditable) {
            el.textContent = VALUE;
        } else if (tag === 'input' || tag === 'textarea' || tag === 'select') {
            // Use the native setter so framework-controlled inputs (React, Vue, ...) see the change
            let proto = Object.getPrototypeOf(el);
            let desc = null;
            while (proto && !(desc = Object.getOwnPropertyDescriptor(proto, 'value'))) proto = Object.getPrototypeOf(proto);
            if (desc && desc.set) desc.set.call(el, VALUE); else el.value = VALUE;
        } else {
            return JSON.stringify({ error: 'element is not a fillable field (' + tag + ')' });
        }
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    } else if (OP === 'type_text') {
        // Simulated keystrokes for masked inputs that rebuild their value from key events
        const typeable = tag === 'textarea' || (tag === 'input' && ['text', 'search', 'tel', 'url', 'password', ''].includes((el.type || '').toLowerCase()));
        if (!typeable) {
            return JSON.stringify({ error: 'element does not support simulated typing (' + tag + ')' });
        }
        const win = el.ownerDocument.defaultView;
        const key = (k, code) => ({ key: k, keyCode: code, which: code, charCode: code, bubbles: true, cancelable: true });
        el.focus();
        el.select();
        el.dispatchEvent(new win.KeyboardEvent('keydown', key('Backspace', 8)));
        el.setRangeText('', 0, el.value.length, 'end');
        el.dispatchEvent(new win.InputEvent('input', { inputType: 'deleteContentBackward', bubbles: true }));
        el.dispatchEvent(new win.KeyboardEvent('keyup', key('Backspace', 8)));
        for (const ch of VALUE) {
            const opts = key(ch, ch.charCodeAt(0));
            const down = el.dispatchEvent(new win.KeyboardEvent('keydown', opts));
            const press = down && el.dispatchEvent(new win.KeyboardEvent('keypress', opts));
            if (press && el.dispatchEvent(new win.InputEvent('beforeinput', { inputType: 'insertText', data: ch, bubbles: true, cancelable: true }))) {
                const start = el.selectionStart ?? el.value.length;
                const end = el.selectionEnd ?? el.value.length;
                el.setRangeText(ch, start, end, 'end');
                el.dispatchEvent(new win.InputEvent('input', { inputType: 'insertText', data: ch, bubbles: true }));
            }
            el.dispatchEvent(new win.KeyboardEvent('keyup', opts));
        }
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }

    const info = {
        tag: tag,
        type: (el.getAttribute('type') || el.type || '').toLowerCase(),
        id: el.id || '',
        name: el.getAttribute('name') || '',
        placeholder: el.getAttribute('placeholder') || '',
        contentEditable: !!el.isContentEditable,
        value: el.isContentEditable ? (el.innerText || '') : String(el.value ?? ''),
        valid: el.validity ? el.validity.valid : true,
        validationMessage: el.validationMessage || ''
    };
    if (tag === 'select') {
        info.options = Array.from(el.options).map(o => ({ text: (o.text || '').trim(), value: o.value }));
        info.selectedText = el.selectedIndex >= 0 ? (el.options[el.selectedIndex].text || '').trim() : '';
    }
    return JSON.stringify(info);
})()
"""

def new_bind_key(ref: str) -> str:
    """Creates a key that scopes a remembered element to one action on one ref."""
    return f"{ref}:{time.time_ns()}:{os.getpid()}"

def _run_bound_js(op: str, value: Optional[str], mode: str, key: str) -> Dict[str, Any]:
    js = (BOUND_ELEMENT_JS
          .replace("__OP__", json.dumps(op))
          .replace("__VALUE__", json.dumps(value if value is not None else ""))
          .replace("__MODE__", json.dumps(mode))
          .replace("__KEY__", json.dumps(key)))
    out = run_cmd([ORCA_BIN, "eval", "--expression", js])
    try:
        data = json.loads(out)
    except (json.JSONDecodeError, TypeError) as e:
        raise OrcaError(f"invalid response ({e})")
    if not isinstance(data, dict):
        raise OrcaError("unexpected response")
    return data

def release_bound_element(key: str):
    """Forgets an element remembered by bind_element so the page does not keep it alive."""
    try:
        run_cmd([ORCA_BIN, "eval", "--expression",
                 f"(() => {{ if (window.__orcaSmartBound) delete window.__orcaSmartBound[{json.dumps(key)}]; return true; }})()"])
    except OrcaError:
        pass

def bind_element(ref: str, op: str = "inspect", value: Optional[str] = None, key: Optional[str] = None) -> Dict[str, Any]:
    """
    Addresses the exact element behind `ref` and inspects it (op='inspect') or sets its
    value with native setters + input/change events (op='set_value').
    With a `key` (see new_bind_key), the element found the first time is reused on later
    calls; otherwise it is located by blurring the page and focusing the ref via Orca.
    Raises OrcaError if the exact element cannot be addressed.
    """
    if key:
        data = _run_bound_js(op, value, "stored", key)
        if data.get("error") != "no-stored-element":
            if data.get("error"):
                raise OrcaError(f"Could not address element [{ref}] in the page: {data['error']}")
            return data

    try:
        run_cmd([ORCA_BIN, "eval", "--expression", BLUR_ACTIVE_JS])
    except OrcaError:
        pass
    run_orca_json(["focus", "--element", ref])
    data = _run_bound_js(op, value, "focused", key or "")
    if data.get("error"):
        raise OrcaError(f"Could not address element [{ref}] in the page: {data['error']}")
    return data

def get_orca_snapshot() -> Dict[str, Any]:
    out = run_cmd([ORCA_BIN, "snapshot", "--json"])
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise OrcaError(f"Orca returned invalid JSON snapshot: {e}")

    if not isinstance(data, dict) or "result" not in data:
        raise OrcaError(f"Unexpected snapshot structure from Orca: missing 'result' object.")

    # Automatically enrich unlabelled inputs with surrounding DOM labels (e.g. table/bootstrap layouts).
    # Refs are matched to DOM fields by document order, so only refs and fields that both lack an
    # accessible name take part, and labels are applied only when the two lists line up exactly.
    refs = data.get("result", {}).get("refs", {})
    snapshot_text = data.get("result", {}).get("snapshot", "") or ""
    ref_order = re.findall(r'ref=(e\d+)', snapshot_text)
    unlabelled_refs = [
        ref_id for ref_id in dict.fromkeys(ref_order)
        if ref_id in refs
        and refs[ref_id].get("role") in ["textbox", "combobox", "searchbox", "search"]
        and not refs[ref_id].get("name", "").strip()
    ]

    if unlabelled_refs:
        js = """
        (() => {
            const SKIP_TYPES = ['hidden', 'checkbox', 'radio', 'button', 'submit', 'reset', 'image', 'file', 'range', 'color'];
            const fields = [];
            const walk = (doc) => {
                doc.querySelectorAll("input, select, textarea, iframe, frame").forEach(el => {
                    if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') {
                        try { if (el.contentDocument) walk(el.contentDocument); } catch (e) {}
                        return;
                    }
                    if (el.tagName === 'INPUT' && SKIP_TYPES.includes((el.type || '').toLowerCase())) return;
                    if (el.getClientRects().length === 0) return;
                    const hasName = (el.labels && el.labels.length > 0) || el.getAttribute('aria-label') ||
                        el.getAttribute('aria-labelledby') || el.getAttribute('title') || el.getAttribute('placeholder');
                    if (!hasName) fields.push(el);
                });
            };
            walk(document);
            return fields.map((el, i) => {
                let label = "";
                if (el.parentElement && el.parentElement.previousElementSibling) {
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
            if isinstance(dom_labels, list) and len(dom_labels) == len(unlabelled_refs):
                for ref_id, dom_label in zip(unlabelled_refs, dom_labels):
                    lbl = dom_label.get("resolvedLabel")
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
        // Collect the top document plus every same-origin frame (embedded forms live in iframes)
        const docs = [];
        const walk = (doc) => {
            docs.push(doc);
            doc.querySelectorAll('iframe, frame').forEach(f => {
                try { if (f.contentDocument && f.contentDocument.body) walk(f.contentDocument); } catch (e) {}
            });
        };
        walk(document);

        const invalidInputs = [];
        const alerts = [];
        const dialogs = [];
        const formValues = {};
        const texts = [];

        docs.forEach((doc, docIdx) => {
            const win = doc.defaultView;
            const prefix = docIdx > 0 ? 'frame' + docIdx + ':' : '';

            // 1. Detect HTML5 invalid form inputs (validity is read without firing 'invalid' events)
            doc.querySelectorAll('input, select, textarea').forEach(el => {
                if (el.validity && !el.validity.valid) {
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
            doc.querySelectorAll(
                '[role="alert"], [role="status"], [aria-live="assertive"], .alert, .error, .error-message, .invalid-feedback, .toast, .notice, .alert-banner'
            ).forEach(el => {
                if (el.offsetParent !== null) { // visible only
                    const txt = el.innerText.trim();
                    if (txt && !alerts.includes(txt) && txt.length < 300) {
                        alerts.push(txt);
                    }
                }
            });

            // 3. Detect open modal dialogs
            doc.querySelectorAll('dialog[open], [role="dialog"], [role="alertdialog"], .modal.show').forEach(d => {
                const isNativeDialogOpen = d.tagName.toLowerCase() === 'dialog' && d.open;
                const isVisibleAriaDialog = d.offsetParent !== null && win.getComputedStyle(d).display !== 'none';
                if (isNativeDialogOpen || isVisibleAriaDialog) {
                    const titleEl = d.querySelector('h1, h2, h3, .modal-title, [id*="title"]');
                    const title = titleEl ? titleEl.innerText.trim() : (d.id || 'Active Dialog');
                    dialogs.push(title);
                }
            });

            // 4. Capture snapshot of all form input values
            doc.querySelectorAll('input:not([type=hidden]), select, textarea').forEach((el, idx) => {
                let label = '';
                if (el.labels && el.labels[0]) label = el.labels[0].innerText.trim();
                else if (el.parentElement && el.parentElement.previousElementSibling) label = el.parentElement.previousElementSibling.innerText.trim();
                let key = prefix + (label || el.name || el.id || ('field_' + idx));
                if (key in formValues) key += '#' + idx;
                const val = (el.type === 'checkbox' || el.type === 'radio') ? el.checked : el.value;
                formValues[key] = String(val);
            });

            texts.push(doc.body ? doc.body.innerText.trim() : '');
        });

        const bodyTxt = texts.join('\\n');
        let hash = 2166136261;
        for (let i = 0; i < bodyTxt.length; i++) {
            hash ^= bodyTxt.charCodeAt(i);
            hash = Math.imul(hash, 16777619) >>> 0;
        }

        return {
            url: window.location.href,
            title: document.title || 'Untitled',
            bodyText: bodyTxt.slice(0, 5000),
            bodyLength: bodyTxt.length,
            bodyHash: hash.toString(16),
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

    # Detect content text change (full-text hash when available, covering text beyond the 5000-char excerpt)
    if before.get("bodyHash") and after.get("bodyHash"):
        content_changed = before["bodyHash"] != after["bodyHash"]
    else:
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

def parse_date_value(val: str) -> Optional[str]:
    """Tries to parse various natural date formats into standard YYYY-MM-DD."""
    val = val.strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}$', val):
        return val

    months = {
        'jan': '01', 'january': '01', 'feb': '02', 'february': '02',
        'mar': '03', 'march': '03', 'apr': '04', 'april': '04',
        'may': '05', 'jun': '06', 'june': '06', 'jul': '07', 'july': '07',
        'aug': '08', 'august': '08', 'sep': '09', 'september': '09',
        'oct': '10', 'october': '10', 'nov': '11', 'november': '11',
        'dec': '12', 'december': '12'
    }

    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', val)
    year = year_match.group(1) if year_match else None

    month = None
    for m_name, m_num in months.items():
        if re.search(r'\b' + m_name + r'\b', val, re.IGNORECASE):
            month = m_num
            break

    cleaned = re.sub(r'\b(19\d{2}|20\d{2})\b', '', val)
    for m_name in months.keys():
        cleaned = re.sub(r'\b' + m_name + r'\b', '', cleaned, flags=re.IGNORECASE)

    day_match = re.search(r'\b(0?[1-9]|[12]\d|3[01])\b', cleaned)
    day = f"{int(day_match.group(1)):02d}" if day_match else None

    if year and month and day:
        return f"{year}-{month}-{day}"

    slash_match = re.search(r'(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})', val)
    if slash_match:
        p1, p2, p3 = slash_match.groups()
        if int(p1) > 12:
            return f"{p3}-{int(p2):02d}-{int(p1):02d}"
        elif int(p2) > 12:
            return f"{p3}-{int(p1):02d}-{int(p2):02d}"
        else:
            return f"{p3}-{int(p2):02d}-{int(p1):02d}"

    return None

def format_date_for_field(iso_date: str, field: Dict[str, Any], raw_value: Optional[str] = None) -> Optional[str]:
    """
    Formats a YYYY-MM-DD date the way the target field expects it, based on the field's
    input type (native date pickers) or a date-pattern placeholder such as 'MM-DD-YYYY'.
    A raw value that is already written in the placeholder's format is kept as-is.
    Returns None when the field gives no indication that it is a date field.
    """
    year, month, day = iso_date.split("-")
    field_type = (field.get("type") or "").lower()
    if field_type == "date":
        return iso_date
    if field_type == "datetime-local":
        return f"{iso_date}T00:00"
    if field_type == "month":
        return f"{year}-{month}"

    placeholder = (field.get("placeholder") or "").strip().upper()
    m = re.fullmatch(r'(MM|DD|YYYY)([-/. ])(MM|DD|YYYY)\2(MM|DD|YYYY)', placeholder)
    if m and sorted([m.group(1), m.group(3), m.group(4)]) == ["DD", "MM", "YYYY"]:
        order = (m.group(1), m.group(3), m.group(4))
        # A numeric date already written in the field's own order is read in that order,
        # whatever separator was used (so 03/04/2020 and 03-04-2020 mean the same date)
        raw_match = re.fullmatch(r'(\d{1,4})[-/.](\d{1,4})[-/.](\d{1,4})', (raw_value or "").strip())
        if raw_match:
            raw_parts = dict(zip(order, raw_match.groups()))
            if (len(raw_parts["YYYY"]) == 4 and len(raw_parts["MM"]) <= 2 and len(raw_parts["DD"]) <= 2
                    and 1 <= int(raw_parts["MM"]) <= 12 and 1 <= int(raw_parts["DD"]) <= 31):
                parts = {"YYYY": raw_parts["YYYY"], "MM": raw_parts["MM"].zfill(2), "DD": raw_parts["DD"].zfill(2)}
                return m.group(2).join(parts[g] for g in order)
        parts = {"YYYY": year, "MM": month, "DD": day}
        return m.group(2).join(parts[g] for g in (m.group(1), m.group(3), m.group(4)))
    return None

def extract_snapshot_metadata(snapshot_text: str) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, bool], Dict[str, bool]]:
    ref_contexts = {}
    ref_values = {}
    ref_disabled = {}
    ref_checked = {}
    stack = []
    last_label = ""

    for line in snapshot_text.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        while stack and stack[-1][0] >= indent:
            stack.pop()

        m_label = re.search(r'([A-Za-z]+)\s+"([^"]+)"', line)
        current_label = m_label.group(2) if m_label else ""
        if current_label and ("StaticText" in line or "Label" in line):
            last_label = current_label

        # Attributes live only in the bracket that holds the ref, e.g. [checked=false, disabled, ref=e3];
        # the quoted element name is never inspected, so a label like "Show disabled items" is not a flag.
        m_ref = re.search(r'\[([^\[\]]*?)(?:ref=)?(e\d+)\](?::\s*(.+))?', line)
        if m_ref:
            ref_id = m_ref.group(2)
            val = m_ref.group(3)
            if val:
                ref_values[ref_id] = val.strip()
            attrs = {a.strip().lower() for a in m_ref.group(1).split(",") if a.strip()}
            if "disabled" in attrs or "disabled=true" in attrs:
                ref_disabled[ref_id] = True
            if "checked" in attrs or "checked=true" in attrs:
                ref_checked[ref_id] = True
            elif "checked=false" in attrs:
                ref_checked[ref_id] = False
            ancestors = [item[1] for item in stack if item[1]]
            context = " ".join(ancestors) if ancestors else last_label
            ref_contexts[ref_id] = context

        if current_label:
            stack.append((indent, current_label))

    return ref_contexts, ref_values, ref_disabled, ref_checked

def smart_dom_prune(snapshot_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = snapshot_data.get("result", {})
    refs = result.get("refs", {})
    snapshot_text = result.get("snapshot", "")
    ref_contexts, ref_values, ref_disabled, ref_checked = extract_snapshot_metadata(snapshot_text) if snapshot_text else ({}, {}, {}, {})
    pruned = []

    for ref_id, info in refs.items():
        role = info.get("role", "").lower()
        name = info.get("name", "").strip()

        # If name is empty, recover label from snapshot text (e.g. ": Select Year")
        if not name and ref_id in ref_values:
            name = ref_values[ref_id]

        if role in ["generic", "none", "presentation"] and not name:
            continue

        is_input = role in ["combobox", "textbox", "searchbox", "search", "spinbutton", "date"]
        is_button = role in ["button", "menuitem"]
        is_link = role == "link"
        is_checkable = role in ["checkbox", "radio", "switch"]

        inline_val = ref_values.get(ref_id, "")
        context = ref_contexts.get(ref_id, "")

        if role == "spinbutton" and context and context.lower() not in name.lower():
            # Native date/time inputs are exposed only as Month/Day/Year spinbuttons;
            # their label lives in the surrounding context, so keep it in the name.
            display_name = f"{context} {name}".strip()
            semantic_text = f"{context} {name} {role}".strip()
        elif inline_val and inline_val.lower() not in name.lower():
            display_name = f"{name} ({inline_val})".strip()
            semantic_text = f"{context} {name} {inline_val} {role}".strip()
        elif context and context.lower() not in name.lower():
            display_name = f"{context} {name}".strip()
            semantic_text = f"{context} {name} {role}".strip()
        else:
            display_name = name
            semantic_text = f"{context} {name} {role}".strip()
        if not semantic_text:
            continue

        if is_checkable:
            action_type = "check"
        elif is_input:
            action_type = "fill"
        else:
            action_type = "click"

        pruned.append({
            "ref": ref_id,
            "role": role,
            "name": display_name,
            "raw_name": name,
            "context": context,
            "disabled": ref_disabled.get(ref_id, False),
            "checked": ref_checked.get(ref_id, None),
            "is_input": is_input,
            "is_button": is_button,
            "is_link": is_link,
            "is_checkable": is_checkable,
            "is_interactive": (is_input or is_button or is_link or is_checkable),
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

    # Exact full match bonus on direct element name vs ancestor context
    raw_name_text = element.get("raw_name", "").lower()
    if query.strip().lower() == raw_name_text and raw_name_text:
        score += 8.0
        substantive_matched = True
    elif query.strip().lower() == name_text:
        score += 5.0
        substantive_matched = True
    elif raw_name_text and query.strip().lower() in raw_name_text:
        score += 4.0
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

def resolve_target_element(target: str, confidence_threshold: float = 1.0, require: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """
    Resolves target query to element ref.
    Guards against ambiguity when multiple elements have similar high scores.
    `require` restricts semantic matches to one kind of element ('is_input' for fills,
    'is_checkable' for checkboxes/radios); direct refs are always honoured.
    """
    clean = target.strip().lstrip("@")
    snapshot = get_orca_snapshot()
    refs = snapshot.get("result", {}).get("refs", {})

    pruned = smart_dom_prune(snapshot)

    # Direct ref check (e.g. 'e3'); prefer the pruned entry so disabled/checked state is kept
    if clean in refs:
        for el in pruned:
            if el["ref"] == clean:
                return clean, el
        return clean, refs[clean]

    scored = []
    for el in pruned:
        score = compute_semantic_score(target, el)
        if score >= confidence_threshold:
            scored.append((score, el))

    if not scored:
        raise ElementNotFoundError(f"Could not find any element matching query: '{target}'", target=target)

    scored.sort(key=lambda x: x[0], reverse=True)

    if require:
        kind_matches = [(s, el) for s, el in scored if el.get(require)]
        if not kind_matches:
            kind_desc = "input field" if require == "is_input" else "checkbox, radio, or switch"
            closest = scored[0][1]
            raise ElementNotFoundError(
                f"Could not find any {kind_desc} matching query: '{target}' "
                f"(closest match is [{closest['ref']}] \"{closest['name']}\" ({closest['role']}))",
                target=target
            )
        scored = kind_matches

    # Ambiguity check: if top 2 candidates have distinct names and near-identical scores
    if len(scored) >= 2:
        top1_score, top1_el = scored[0]
        top2_score, top2_el = scored[1]
        score_diff = abs(top1_score - top2_score)

        if score_diff < 0.15 and top1_el["name"].lower() != top2_el["name"].lower():
            # If both are sub-parts belonging to the same parent context, don't block as ambiguous
            same_context = bool(top1_el.get("context")) and top1_el.get("context") == top2_el.get("context")
            if not same_context:
                candidates = [f"[{el['ref']}] \"{el['name']}\" ({el['role']}) (score: {s})" for s, el in scored[:3] if abs(s - top1_score) < 0.15]
                raise AmbiguousTargetError(
                    f"Query '{target}' is ambiguous between multiple elements:\n  • " +
                    "\n  • ".join(candidates) +
                    "\nPlease provide a more specific label or use the element ref directly.",
                    candidates=candidates
                )

    best = scored[0][1]
    return best["ref"], best

def act_click_and_verify(target: str, wait_seconds: float = 1.0) -> Dict[str, Any]:
    print(f"\n🎯 Finding target element for click: '{target}'...")
    ref, info = resolve_target_element(target)
    name = info.get("name") or info.get("role")
    role = info.get("role", "")
    print(f"👉 Matched element: [{ref}] \"{name}\" ({role})")

    if info.get("disabled"):
        print(f"\n⚠️  ELEMENT DISABLED: Element [{ref}] \"{name}\" ({role}) is currently disabled on this page.")
        raise ElementDisabledError(f"Cannot click '{target}' because element [{ref}] is disabled on the page.", target=target)

    # Check if this is a submission action
    is_submit = "submit" in target.lower() or "submit" in name.lower() or "save" in target.lower()

    # Auto-scroll the exact element into view so clicks never miss off-screen elements
    try:
        run_cmd([ORCA_BIN, "scrollintoview", "--element", ref])
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

def _values_match(expected: str, actual: Optional[str]) -> bool:
    """Loose comparison that tolerates input masks / formatting (e.g. '(987) 654-3210' vs '9876543210')."""
    if actual is None:
        return False
    if expected == actual:
        return True
    norm_expected = re.sub(r'[^0-9a-z]', '', expected.lower())
    norm_actual = re.sub(r'[^0-9a-z]', '', actual.lower())
    return norm_expected != "" and norm_expected == norm_actual

def _read_value(ref: str) -> Optional[str]:
    try:
        val = run_orca_json(["get", "--element", ref, "--what", "value"]).get("value")
        return val if isinstance(val, str) else None
    except OrcaError:
        return None

def _match_select_option(options: List[Dict[str, str]], desired: str) -> Optional[Dict[str, str]]:
    """Finds a dropdown option by exact text, then exact value, then text prefix (case-insensitive)."""
    wanted = desired.strip().lower()
    for key in ("text", "value"):
        for opt in options:
            if (opt.get(key) or "").strip().lower() == wanted:
                return opt
    if wanted:
        for opt in options:
            if (opt.get("text") or "").strip().lower().startswith(wanted):
                return opt
    return None

def _fill_select(ref: str, name: str, value: str, field: Dict[str, Any], key: Optional[str] = None) -> Dict[str, Any]:
    """Selects an option in a native <select> and verifies the selection on the element itself."""
    options = field.get("options", [])
    opt = _match_select_option(options, value)
    if not opt:
        avail = [o.get("text") or o.get("value") for o in options if (o.get("text") or o.get("value"))]
        avail_str = ", ".join(avail[:10]) + ("..." if len(avail) > 10 else "")
        print(f"\n❌ OPTION UNAVAILABLE: Option \"{value}\" is not available for \"{name}\".")
        print(f"   Available options ({len(avail)}): [{avail_str}]")
        raise OptionUnavailableError(
            f"Option '{value}' is not available in dropdown '{name}'. "
            f"Available options are: [{avail_str}]",
            available_options=avail,
            target=value,
            element=name
        )

    def is_selected(state: Dict[str, Any]) -> bool:
        return state.get("value") == opt["value"] and state.get("selectedText") == opt["text"]

    select_value = opt["value"] or opt["text"]
    print(f"⚡ Executing: orca select --element {ref} --value \"{select_value}\"")
    try:
        run_orca_json(["select", "--element", ref, "--value", select_value])
    except OrcaError as e:
        print(f"   ↪ orca select failed ({e})")

    after = bind_element(ref, key=key)
    if not is_selected(after):
        print("   ↪ Selection did not take effect; setting the option directly on the element")
        after = bind_element(ref, op="set_value", value=opt["value"], key=key)
        if not is_selected(after):
            raise OrcaError(
                f"Could not select '{opt['text']}' in [{ref}] \"{name}\": "
                f"the field shows '{after.get('selectedText')}'."
            )
    print(f"⚡ Selected dropdown option: \"{opt['text']}\"")
    return after

def _fill_text(ref: str, value: str, field: Optional[Dict[str, Any]], key: Optional[str] = None) -> Dict[str, Any]:
    """
    Fills a text-like field via Orca, then reads the value back from the same element.
    If the value did not stick (some iframes / custom widgets ignore Orca's fill), it is set
    directly on the exact element. Raises OrcaError if the field still does not hold the value.
    """
    print(f"⚡ Executing: orca fill --element {ref} --value \"{value}\"")
    fill_error = None
    try:
        run_cmd([ORCA_BIN, "fill", "--element", ref, "--value", value])
    except OrcaError as e:
        fill_error = e

    if field is None:
        # The element could not be addressed in the page; rely on Orca's own read-back
        if fill_error:
            raise fill_error
        actual = _read_value(ref)
        if actual is not None and not _values_match(value, actual):
            raise OrcaError(f"Value did not stick in [{ref}]: the field shows '{actual}'.")
        return {"value": actual if actual is not None else value}

    after = _inspect_or_read(ref, key)
    if _values_match(value, after.get("value")):
        return after

    print("   ↪ orca fill did not take effect; setting the value directly on the element")
    try:
        after = bind_element(ref, op="set_value", value=value, key=key)
    except OrcaError as e:
        # Some widgets (e.g. native date segments) briefly refuse focus right after a fill;
        # the earlier fill may also have landed late, so re-check before giving up.
        time.sleep(0.3)
        after = _inspect_or_read(ref, key)
        if not _values_match(value, after.get("value")):
            raise e
    if _values_match(value, after.get("value")):
        return after

    # Masked inputs (dates, phone numbers, ...) rebuild their value from keystrokes
    print("   ↪ Field rewrote the value (input mask?); typing it key by key instead")
    after = bind_element(ref, op="type_text", value=value, key=key)
    if not _values_match(value, after.get("value")):
        raise OrcaError(f"Value did not stick in [{ref}]: the field shows '{after.get('value')}'.")
    return after

def _inspect_or_read(ref: str, key: Optional[str] = None) -> Dict[str, Any]:
    """Inspects the exact element, falling back to Orca's own value read-back."""
    try:
        return bind_element(ref, key=key)
    except OrcaError:
        return {"value": _read_value(ref)}

def act_fill_and_verify(target: str, value: str) -> Dict[str, Any]:
    print(f"\n🎯 Finding target field for fill: '{target}'...")
    ref, info = resolve_target_element(target, require="is_input")
    name = info.get("name") or info.get("role")
    role = info.get("role", "")
    print(f"👉 Matched input field: [{ref}] \"{name}\" ({role})")

    if info.get("disabled"):
        print(f"\n⚠️  ELEMENT DISABLED: Element [{ref}] \"{name}\" ({role}) is currently disabled on this page.")
        raise ElementDisabledError(f"Cannot fill '{target}' because element [{ref}] is disabled on the page.", target=target)

    before_state = get_page_verification_state()

    key = new_bind_key(ref)
    try:
        field = None
        try:
            field = bind_element(ref, key=key)
        except OrcaError as e:
            print(f"ℹ️  Could not inspect the field in the page ({e}); relying on Orca's fill.")

        if field:
            tag, field_type = field.get("tag"), field.get("type")
            if field_type in ("checkbox", "radio"):
                raise OrcaError(f"Element [{ref}] \"{name}\" is a {field_type}; use --check / --uncheck instead of --fill.")
            fillable = field.get("contentEditable") or tag in ("textarea", "select") or (
                tag == "input" and field_type not in ("button", "submit", "reset", "image", "file", "hidden"))
            if not fillable:
                raise OrcaError(f"Element [{ref}] \"{name}\" ({tag}) is not a fillable field.")

        if field and field.get("tag") == "select":
            final_field = _fill_select(ref, name, value, field, key)
            actual_filled_val = final_field.get("selectedText") or value
        else:
            fill_value = value
            if field:
                iso_date = parse_date_value(value)
                formatted = format_date_for_field(iso_date, field, raw_value=value) if iso_date else None
                if formatted and formatted != value:
                    print(f"📅 Date field detected; entering \"{value}\" as \"{formatted}\"")
                    fill_value = formatted
            final_field = _fill_text(ref, fill_value, field, key)
            actual_filled_val = final_field.get("value") or fill_value

    finally:
        release_bound_element(key)

    time.sleep(0.3)
    after_state = get_page_verification_state()
    comparison = compare_page_states(before_state, after_state)
    comparison["filled_value"] = actual_filled_val
    comparison["field_valid"] = final_field.get("valid")
    comparison["field_validation_message"] = final_field.get("validationMessage", "")

    print("\n" + "=" * 70)
    print(" 🔎 INPUT VERIFICATION REPORT")
    print("=" * 70)
    print(f"✅ Filled element [{ref}] with: \"{actual_filled_val}\"")
    if final_field.get("valid") is False:
        print(f"⚠️  Field has validation warning: {final_field.get('validationMessage') or 'Invalid value'}")
    elif final_field.get("valid") is True:
        print("✅ Field validity: OK")
    else:
        print("ℹ️  Field validity: not checked (field could not be inspected)")
    print("=" * 70)
    return comparison

def _read_checked(ref: str) -> Optional[bool]:
    try:
        val = run_orca_json(["is", "--element", ref, "--what", "checked"]).get("checked")
        return val if isinstance(val, bool) else None
    except OrcaError:
        return None

def _state_label(state: Optional[bool]) -> str:
    if state is True:
        return "Checked (true)"
    if state is False:
        return "Unchecked (false)"
    return "Unknown"

def act_checkbox_radio(target: str, action: str = "check") -> Dict[str, Any]:
    """
    Toggles or sets the checked state of a checkbox, radio button, or switch.
    action can be: 'check', 'uncheck', or 'toggle'.
    Acts on the exact element behind the resolved ref (works inside iframes) and
    verifies the resulting state on that same element.
    """
    action = action.lower()
    if action not in ["check", "uncheck", "toggle"]:
        raise ValueError(f"Invalid action: '{action}'. Choose from 'check', 'uncheck', or 'toggle'.")

    print(f"\n🎯 Finding target element for {action}: '{target}'...")
    ref, info = resolve_target_element(target, require="is_checkable")
    name = info.get("name") or info.get("role")
    role = info.get("role", "")
    print(f"👉 Matched element: [{ref}] \"{name}\" ({role})")

    if info.get("disabled"):
        print(f"\n⚠️  ELEMENT DISABLED: Element [{ref}] \"{name}\" ({role}) is currently disabled on this page.")
        raise ElementDisabledError(f"Cannot {action} '{target}' because element [{ref}] is disabled on the page.", target=target)

    initial_checked = _read_checked(ref)
    if initial_checked is None:
        initial_checked = info.get("checked")

    if action == "check":
        desired = True
    elif action == "uncheck":
        desired = False
    else:
        desired = (not initial_checked) if initial_checked is not None else None

    def report(final_checked: Optional[bool], changed: bool):
        print("\n" + "=" * 70)
        print(" ☑️  CHECKBOX / RADIO ACTION REPORT")
        print("=" * 70)
        print(f"Target Element : [{ref}] \"{name}\" ({role})")
        print(f"Action Taken   : {action.upper()}")
        print(f"Initial State  : {_state_label(initial_checked)}")
        print(f"Final State    : {_state_label(final_checked)}")
        if not changed:
            print(f"Status         : ℹ️ ALREADY {action.upper()}ED (No change needed)")
        elif final_checked is None:
            print(f"Status         : ⚠️ {action.upper()} SENT (final state could not be read back)")
        else:
            print(f"Status         : ✅ SUCCESSFULLY {action.upper()}ED")
        print("=" * 70)

    if desired is not None and initial_checked == desired:
        report(initial_checked, changed=False)
        return {"success": True, "changed": False, "initialState": initial_checked, "finalState": initial_checked}

    if role == "radio" and desired is False:
        raise OrcaError(
            f"Radio button [{ref}] \"{name}\" cannot be unchecked directly; "
            f"select another option in its group instead."
        )

    clicked = False
    if desired is None:
        print(f"⚡ Executing: orca click --element {ref}")
        run_cmd([ORCA_BIN, "click", "--element", ref])
        clicked = True
    else:
        cmd = "check" if desired else "uncheck"
        print(f"⚡ Executing: orca {cmd} --element {ref}")
        try:
            run_orca_json([cmd, "--element", ref])
        except OrcaError as e:
            print(f"   ↪ orca {cmd} failed ({e}); clicking the element instead")
            run_cmd([ORCA_BIN, "click", "--element", ref])
            clicked = True
    time.sleep(0.3)

    final_checked = _read_checked(ref)
    if desired is not None and final_checked is not None and final_checked != desired and not clicked:
        print("   ↪ State did not change; clicking the element instead")
        run_cmd([ORCA_BIN, "click", "--element", ref])
        time.sleep(0.3)
        final_checked = _read_checked(ref)
    if desired is not None and final_checked is not None and final_checked != desired:
        raise OrcaError(
            f"Could not {action} [{ref}] \"{name}\": it is still "
            f"{'checked' if final_checked else 'unchecked'}."
        )

    changed = (initial_checked != final_checked) if (initial_checked is not None and final_checked is not None) else True
    report(final_checked, changed)

    return {
        "success": True,
        "changed": changed,
        "initialState": initial_checked,
        "finalState": final_checked
    }

def act_fill_form(form_input: str) -> Dict[str, Any]:
    """
    Fills multiple form fields in sequence from a JSON string or JSON file path.
    Seamlessly handles text inputs, dropdowns, dates, and boolean checkboxes.
    """
    raw_json = form_input.strip()
    if os.path.exists(raw_json) and os.path.isfile(raw_json):
        with open(raw_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as e:
            raise OrcaError(f"Invalid JSON provided for --fill-form: {e}")

    if not isinstance(data, dict):
        raise OrcaError("--fill-form expects a JSON object of key-value pairs (e.g. '{\"Field\": \"Value\"}')")

    print("\n" + "=" * 70)
    print(" 📝 STARTING BATCH FORM FILL")
    print(f" Total Fields to Populate: {len(data)}")
    print("=" * 70)

    results = []
    success_count = 0

    for i, (target, val) in enumerate(data.items(), 1):
        if isinstance(val, bool):
            action_desc = "CHECK" if val else "UNCHECK"
            print(f"\n--- [{i}/{len(data)}] Setting checkbox '{target}' ➔ {action_desc} ---")
            try:
                res = act_checkbox_radio(target, "check" if val else "uncheck")
                results.append({
                    "field": target,
                    "value": val,
                    "status": "SUCCESS",
                    "error": None
                })
                success_count += 1
            except Exception as e:
                print(f"❌ Failed to set checkbox '{target}': {e}")
                results.append({
                    "field": target,
                    "value": val,
                    "status": "FAILED",
                    "error": str(e)
                })
        else:
            str_val = str(val)
            print(f"\n--- [{i}/{len(data)}] Filling field '{target}' ➔ '{str_val}' ---")
            try:
                res = act_fill_and_verify(target, str_val)
                results.append({
                    "field": target,
                    "value": str_val,
                    "status": "SUCCESS",
                    "error": None
                })
                success_count += 1
            except Exception as e:
                print(f"❌ Failed to fill field '{target}': {e}")
                results.append({
                    "field": target,
                    "value": str_val,
                    "status": "FAILED",
                    "error": str(e)
                })

    time.sleep(0.3)
    final_state = get_page_verification_state()
    invalid_inputs = final_state.get("invalidInputs", [])

    print("\n" + "=" * 70)
    print(" 📊 BATCH FORM FILL SUMMARY REPORT")
    print("=" * 70)
    print(f"Success Rate   : {success_count}/{len(data)} fields populated successfully.")
    for r in results:
        icon = "✅" if r["status"] == "SUCCESS" else "❌"
        err_msg = f" (Error: {r['error']})" if r["error"] else ""
        print(f" {icon} Field: \"{r['field']}\" ➔ \"{r['value']}\"{err_msg}")

    if invalid_inputs:
        print("\n⚠️  Remaining Invalid Fields Detected:")
        for inp in invalid_inputs:
            name = inp["label"] or inp["name"] or inp["id"]
            print(f"   • \"{name}\": {inp['validationMessage']}")
    else:
        print("\n✨ Clean: All form fields populated without validation errors!")
    print("=" * 70)

    return {
        "total": len(data),
        "success_count": success_count,
        "results": results,
        "invalid_inputs": invalid_inputs
    }

def act_wait_for(target: str, timeout: float = 10.0, poll_interval: float = 0.5, condition: str = "visible") -> Dict[str, Any]:
    """
    Waits asynchronously for an element, text, or selector to appear ('visible') or disappear ('gone').
    Also monitors and detects active loading spinners.
    """
    condition = condition.lower()
    start_time = time.time()
    deadline = start_time + timeout

    print(f"\n⏳ Waiting for condition '{condition.upper()}' on: '{target}' (timeout: {timeout:.1f}s)...")

    js_check = rf"""
    (() => {{
        const target = {json.dumps(target)};
        const condition = {json.dumps(condition)};

        const spinners = document.querySelectorAll(
            '.spinner, .loading, .loader, [role="progressbar"], .mat-progress-bar, .ant-spin'
        );
        let spinnerActive = false;
        spinners.forEach(s => {{
            if (s.offsetParent !== null && window.getComputedStyle(s).display !== 'none') {{
                spinnerActive = true;
            }}
        }});

        const bodyText = document.body ? document.body.innerText : '';
        const hasText = bodyText.toLowerCase().includes(target.toLowerCase());

        let hasSelector = false;
        try {{
            const el = document.querySelector(target);
            if (el && el.offsetParent !== null && window.getComputedStyle(el).display !== 'none') {{
                hasSelector = true;
            }}
        }} catch(e) {{}}

        const isPresent = hasText || hasSelector;

        return {{
            target: target,
            isPresent: isPresent,
            spinnerActive: spinnerActive,
            matchedType: hasSelector ? 'selector' : (hasText ? 'text' : 'none')
        }};
    }})()
    """

    found = False
    spinner_seen = False

    while time.time() < deadline:
        try:
            raw = run_cmd([ORCA_BIN, "eval", "--expression", js_check])
            res = json.loads(raw)
            if res.get("spinnerActive"):
                spinner_seen = True

            if condition == "visible" and res.get("isPresent"):
                found = True
                break
            elif condition == "gone" and not res.get("isPresent"):
                found = True
                break
        except Exception:
            pass

        time.sleep(poll_interval)

    elapsed = round(time.time() - start_time, 2)

    print("\n" + "=" * 70)
    print(" ⏳ SMART ASYNC WAIT REPORT")
    print("=" * 70)
    print(f"Target Query   : \"{target}\"")
    print(f"Condition      : {condition.upper()}")
    print(f"Elapsed Time   : {elapsed}s (Timeout: {timeout}s)")
    if spinner_seen:
        print("Spinner Status : 🔄 Active loader/spinner observed during wait")

    if found:
        print(f"Status         : ✅ CONDITION MET ({condition.upper()})")
    else:
        print(f"Status         : ⏱️  TIMED OUT (Condition not met within {timeout}s)")
    print("=" * 70)

    if not found:
        raise OrcaError(f"Wait timed out after {elapsed}s waiting for '{target}' to be {condition}.")

    return {
        "success": found,
        "elapsed": elapsed,
        "condition": condition,
        "target": target,
        "spinner_seen": spinner_seen
    }

def act_keypress(key: str, target: Optional[str] = None) -> Dict[str, Any]:
    """
    Sends a keyboard keypress event to the browser.
    If target is provided, finds the target element, checks if disabled,
    scrolls it into view, and focuses it before sending the keypress.
    Also performs pre- and post-action state verification.
    """
    ref = None
    info = None
    if target:
        print(f"\n🎯 Finding target element for keypress '{key}': '{target}'...")
        ref, info = resolve_target_element(target)
        name = info.get("name") or info.get("role")
        role = info.get("role", "")
        print(f"👉 Matched element: [{ref}] \"{name}\" ({role})")

        if info.get("disabled"):
            print(f"\n⚠️  ELEMENT DISABLED: Element [{ref}] \"{name}\" ({role}) is currently disabled on this page.")
            raise ElementDisabledError(f"Cannot press key '{key}' on '{target}' because element [{ref}] is disabled.", target=target)

        # Focus the exact element so the key goes to it, not to whatever had focus before
        try:
            run_cmd([ORCA_BIN, "scrollintoview", "--element", ref])
        except Exception:
            pass
        run_cmd([ORCA_BIN, "focus", "--element", ref])

    pre_state = get_page_verification_state()

    print(f"⚡ Executing: orca keypress --key {key}")
    run_cmd([ORCA_BIN, "keypress", "--key", key])
    time.sleep(0.3)

    post_state = get_page_verification_state()
    comparison = compare_page_states(pre_state, post_state)

    print("\n" + "=" * 70)
    print(" ⌨️  KEYPRESS ACTION REPORT")
    print("=" * 70)
    print(f"Key Pressed    : {key}")
    if target and info:
        print(f"Target Element : [{ref}] \"{info.get('name')}\" ({info.get('role')})")
    else:
        print(f"Target Element : (Active Browser Focus)")
    print(f"Status         : ✅ KEYPRESS DISPATCHED")
    print(f"Action Outcome : {comparison.get('verdict')}")
    if comparison.get("url_changed"):
        print(f"Navigated To   : {comparison.get('to_url')}")
    if comparison.get("new_error_alerts"):
        print(f"Error Alerts   : {'; '.join(comparison['new_error_alerts'])}")
    print("=" * 70)

    return {
        "success": True,
        "key": key,
        "target": target,
        "target_ref": ref,
        "target_name": info.get("name") if info else None,
        "comparison": comparison
    }

def act_screenshot(output_path: Optional[str] = None, format: str = "png") -> Dict[str, Any]:
    """
    Captures a viewport screenshot of the active browser tab via Orca CLI,
    decodes the base64 payload, and saves it to a file.
    """
    format = format.lower()
    if format not in ["png", "jpeg", "jpg"]:
        format = "png"
    if format == "jpg":
        format = "jpeg"

    now = datetime.datetime.now()
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    timestamp_iso = now.isoformat()

    if not output_path:
        out_dir = os.path.join(os.getcwd(), "screenshots")
        os.makedirs(out_dir, exist_ok=True)
        ext = "jpg" if format == "jpeg" else "png"
        output_path = os.path.join(out_dir, f"screenshot_{timestamp_str}.{ext}")
    else:
        parent = os.path.dirname(os.path.abspath(output_path))
        if parent:
            os.makedirs(parent, exist_ok=True)

    print(f"\n📸 Capturing {format.upper()} screenshot via Orca CLI...")
    raw = run_cmd([ORCA_BIN, "screenshot", "--format", format, "--json"])

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise OrcaError(f"Failed to parse screenshot JSON output: {e}")

    result = data.get("result", {})
    b64_data = result.get("data")
    if not b64_data:
        raise OrcaError(f"Orca screenshot response missing image data: {raw[:200]}")

    try:
        img_bytes = base64.b64decode(b64_data)
    except Exception as e:
        raise OrcaError(f"Failed to decode base64 screenshot data: {e}")

    with open(output_path, "wb") as f:
        f.write(img_bytes)

    size_bytes = len(img_bytes)
    size_kb = round(size_bytes / 1024, 1)
    abs_path = os.path.abspath(output_path)

    print("\n" + "=" * 70)
    print(" 📸 VIEWPORT SCREENSHOT CAPTURED")
    print("=" * 70)
    print(f"File Path      : {abs_path}")
    print(f"Image Format   : {format.upper()}")
    print(f"File Size      : {size_bytes} bytes ({size_kb} KB)")
    print(f"Timestamp      : {timestamp_iso}")
    print("=" * 70)

    return {
        "success": True,
        "path": abs_path,
        "format": format,
        "size_bytes": size_bytes,
        "timestamp": timestamp_iso
    }

def check_errors_now() -> Dict[str, Any]:
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
    return state

def describe_current_page() -> Dict[str, Any]:
    snapshot = get_orca_snapshot()
    elements = smart_dom_prune(snapshot)
    state = get_page_verification_state()

    inputs = [el for el in elements if el["is_input"]]
    checkables = [el for el in elements if el.get("is_checkable")]
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
    elif len(inputs) + len(checkables) >= 2 or any("form" in state["url"].lower() for _ in [1]):
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
    print(f"📊 Elements Found   : {len(inputs)} Inputs/Fields | {len(checkables)} Checkboxes/Radios | {len(buttons)} Buttons | {len(links)} Links")

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

    if checkables:
        print("\n☑️  AVAILABLE CHECKBOXES & RADIOS:")
        for i, chk in enumerate(checkables[:6], 1):
            ref = chk["ref"]
            name = chk["name"] or "Unnamed option"
            role = chk["role"]
            print(f"   [{i}] Ref: [{ref}] | Type: {role:<10} | Label: \"{name}\"")
            print(f"       👉 Toggle with: python3 orca_smart_search.py --toggle \"{name}\"")

    print("\n🔘 KEY ACTION BUTTONS (Top 6):")
    if not buttons:
        print("   (No buttons detected)")
    else:
        for b in buttons[:6]:
            print(f"   - [{b['ref']}] \"{b['name']}\" (python3 orca_smart_search.py --click \"{b['name']}\")")
    print("=" * 75)

    return {
        "title": state.get("title", ""),
        "url": state.get("url", ""),
        "page_type": page_type,
        "total_elements": len(elements),
        "inputs": inputs,
        "buttons": buttons,
        "links": links,
        "checkables": checkables,
        "dialogs": state.get("dialogs", []),
        "alerts": state.get("alerts", []),
        "invalid_inputs": state.get("invalidInputs", [])
    }

def search_elements(query: str, top_k: int = 5) -> Dict[str, Any]:
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
        return {
            "query": query,
            "origin": origin,
            "total_pruned": len(pruned),
            "matches": []
        }

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

    matches = [
        {
            "rank": i,
            "score": round(score, 3),
            "ref": el["ref"],
            "role": el["role"],
            "name": el["name"],
            "suggested_action": el["suggested_action"]
        }
        for i, (score, el) in enumerate(scored[:top_k], 1)
    ]
    return {
        "query": query,
        "origin": origin,
        "total_pruned": len(pruned),
        "matches": matches
    }

def main():
    global IS_JSON_MODE
    raw_args = sys.argv[1:]
    args = []
    for a in raw_args:
        if a in ["--json", "-j"]:
            IS_JSON_MODE = True
        else:
            args.append(a)

    try:
        if not args or args[0].lower() in ["--describe", "describe", "-d", "info"]:
            res = describe_current_page()
            if IS_JSON_MODE:
                emit_json({"ok": True, "action": "describe", "result": res})
        elif args[0].lower() in ["--check-errors", "errors", "check"]:
            res = check_errors_now()
            if IS_JSON_MODE:
                emit_json({"ok": True, "action": "check_errors", "result": res})
        elif args[0].lower() in ["--click", "-c"]:
            if len(args) < 2:
                raise OrcaError("Usage: python3 orca_smart_search.py --click <query_or_ref>")
            target = " ".join(args[1:])
            res = act_click_and_verify(target)
            if IS_JSON_MODE:
                emit_json({"ok": True, "action": "click", "target": target, "result": res})
        elif args[0].lower() in ["--fill", "-f"]:
            if len(args) < 3:
                raise OrcaError("Usage: python3 orca_smart_search.py --fill <query_or_ref> <value>")
            target = args[1]
            value = " ".join(args[2:])
            res = act_fill_and_verify(target, value)
            if IS_JSON_MODE:
                emit_json({"ok": True, "action": "fill", "target": target, "value": value, "result": res})
        elif args[0].lower() in ["--fill-form", "--form", "-ff"]:
            if len(args) < 2:
                raise OrcaError("Usage: python3 orca_smart_search.py --fill-form '<json_string_or_filepath>'")
            form_payload = " ".join(args[1:])
            res = act_fill_form(form_payload)
            if IS_JSON_MODE:
                emit_json({"ok": True, "action": "fill_form", "result": res})
        elif args[0].lower() in ["--check", "-chk"]:
            if len(args) < 2:
                raise OrcaError("Usage: python3 orca_smart_search.py --check <target_query_or_ref>")
            target = " ".join(args[1:])
            res = act_checkbox_radio(target, action="check")
            if IS_JSON_MODE:
                emit_json({"ok": True, "action": "check", "target": target, "result": res})
        elif args[0].lower() in ["--uncheck", "-unchk"]:
            if len(args) < 2:
                raise OrcaError("Usage: python3 orca_smart_search.py --uncheck <target_query_or_ref>")
            target = " ".join(args[1:])
            res = act_checkbox_radio(target, action="uncheck")
            if IS_JSON_MODE:
                emit_json({"ok": True, "action": "uncheck", "target": target, "result": res})
        elif args[0].lower() in ["--toggle", "-t"]:
            if len(args) < 2:
                raise OrcaError("Usage: python3 orca_smart_search.py --toggle <target_query_or_ref>")
            target = " ".join(args[1:])
            res = act_checkbox_radio(target, action="toggle")
            if IS_JSON_MODE:
                emit_json({"ok": True, "action": "toggle", "target": target, "result": res})
        elif args[0].lower() in ["--wait-for", "--wait", "-w"]:
            if len(args) < 2:
                raise OrcaError("Usage: python3 orca_smart_search.py --wait-for <text_or_selector> [timeout_seconds]")
            target = args[1]
            timeout = float(args[2]) if len(args) >= 3 else 10.0
            res = act_wait_for(target, timeout=timeout, condition="visible")
            if IS_JSON_MODE:
                emit_json({"ok": True, "action": "wait_visible", "target": target, "result": res})
        elif args[0].lower() in ["--wait-until-gone", "--wait-gone"]:
            if len(args) < 2:
                raise OrcaError("Usage: python3 orca_smart_search.py --wait-until-gone <text_or_selector> [timeout_seconds]")
            target = args[1]
            timeout = float(args[2]) if len(args) >= 3 else 10.0
            res = act_wait_for(target, timeout=timeout, condition="gone")
            if IS_JSON_MODE:
                emit_json({"ok": True, "action": "wait_gone", "target": target, "result": res})
        elif args[0].lower() in ["--press", "--keypress", "-p"]:
            if len(args) < 2:
                raise OrcaError("Usage: python3 orca_smart_search.py --press <key> [target_query_or_ref]")
            key = args[1]
            target = " ".join(args[2:]) if len(args) >= 3 else None
            res = act_keypress(key, target=target)
            if IS_JSON_MODE:
                emit_json({"ok": True, "action": "keypress", "key": key, "target": target, "result": res})
        elif args[0].lower() in ["--screenshot", "--shot", "-ss"]:
            fmt = "png"
            out_path = None
            i = 1
            while i < len(args):
                if args[i] in ["--format", "-fmt"] and i + 1 < len(args):
                    fmt = args[i + 1].lower()
                    i += 2
                else:
                    out_path = args[i]
                    i += 1
            res = act_screenshot(output_path=out_path, format=fmt)
            if IS_JSON_MODE:
                emit_json({"ok": True, "action": "screenshot", "result": res})
        else:
            query = " ".join(args)
            res = search_elements(query, top_k=5)
            if IS_JSON_MODE:
                emit_json({"ok": True, "action": "search", "result": res})
    except OptionUnavailableError as e:
        if IS_JSON_MODE:
            emit_json({
                "ok": False,
                "error_type": "OptionUnavailableError",
                "error": str(e),
                "available_options": getattr(e, "available_options", []),
                "target": getattr(e, "target", None),
                "element": getattr(e, "element", None),
                "exit_code": 6
            })
        else:
            _builtin_print(f"\n❌ OPTION UNAVAILABLE: {e}", file=sys.stderr)
        sys.exit(6)
    except ElementDisabledError as e:
        if IS_JSON_MODE:
            emit_json({
                "ok": False,
                "error_type": "ElementDisabledError",
                "error": str(e),
                "target": getattr(e, "target", None),
                "exit_code": 7
            })
        else:
            _builtin_print(f"\n⚠️  ELEMENT DISABLED: {e}", file=sys.stderr)
        sys.exit(7)
    except AmbiguousTargetError as e:
        if IS_JSON_MODE:
            emit_json({
                "ok": False,
                "error_type": "AmbiguousTargetError",
                "error": str(e),
                "candidates": getattr(e, "candidates", []),
                "exit_code": 2
            })
        else:
            _builtin_print(f"\n⚠️  AMBIGUITY ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    except ElementNotFoundError as e:
        if IS_JSON_MODE:
            emit_json({
                "ok": False,
                "error_type": "ElementNotFoundError",
                "error": str(e),
                "target": getattr(e, "target", None),
                "exit_code": 3
            })
        else:
            _builtin_print(f"\n❌ NOT FOUND: {e}", file=sys.stderr)
        sys.exit(3)
    except OrcaNotFoundError as e:
        if IS_JSON_MODE:
            emit_json({
                "ok": False,
                "error_type": "OrcaNotFoundError",
                "error": str(e),
                "exit_code": 4
            })
        else:
            _builtin_print(f"\n❌ ORCA CLI ERROR: {e}", file=sys.stderr)
        sys.exit(4)
    except OrcaError as e:
        if IS_JSON_MODE:
            emit_json({
                "ok": False,
                "error_type": "OrcaError",
                "error": str(e),
                "exit_code": 5
            })
        else:
            _builtin_print(f"\n❌ ORCA ERROR: {e}", file=sys.stderr)
        sys.exit(5)
    except Exception as e:
        if IS_JSON_MODE:
            emit_json({
                "ok": False,
                "error_type": type(e).__name__,
                "error": str(e),
                "exit_code": 1
            })
        else:
            _builtin_print(f"\n❌ UNEXPECTED ERROR: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
