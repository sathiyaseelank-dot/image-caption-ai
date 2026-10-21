#!/usr/bin/env python3
"""
Orca Smart DOM Pruner, Page Describer & Semantic Search Engine
--------------------------------------------------------------
- NO LLM required: fast, lightweight, pure algorithmic processing on CPU.
- Can describe the current page (what it is about, structure, available inputs).
- Can perform semantic search on elements and output direct Orca CLI commands.
"""

import sys
import json
import subprocess
import re
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional

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

def get_page_dom_metadata() -> Dict[str, Any]:
    """Extracts high-level page metadata (title, headings, meta tags, forms) directly from browser."""
    js_code = """
    (() => {
        const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
            .map(h => ({ level: h.tagName.toLowerCase(), text: h.innerText.trim() }))
            .filter(h => h.text.length > 0)
            .slice(0, 15);

        const metaDesc = document.querySelector('meta[name="description"]')?.content || '';
        const formsCount = document.querySelectorAll('form').length;
        
        return {
            title: document.title || 'Untitled Page',
            url: window.location.href,
            metaDescription: metaDesc,
            formsCount: formsCount,
            headings: headings
        };
    })()
    """
    try:
        out = run_cmd([ORCA_BIN, "eval", "--expression", js_code])
        return json.loads(out)
    except Exception:
        return {}

def smart_dom_prune(snapshot_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = snapshot_data.get("result", {})
    refs = result.get("refs", {})
    pruned = []

    for ref_id, info in refs.items():
        role = info.get("role", "").lower()
        name = info.get("name", "").strip()

        # Filter out empty/nameless generic nodes
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

def describe_current_page():
    """Analyzes the current page and prints what it is about and all available input fields."""
    snapshot = get_orca_snapshot()
    dom_meta = get_page_dom_metadata()
    elements = smart_dom_prune(snapshot)

    title = dom_meta.get("title") or "Unknown"
    url = dom_meta.get("url") or snapshot.get("result", {}).get("origin", "")
    parsed_url = urlparse(url)
    domain = parsed_url.netloc or "Unknown Domain"

    # Identify page type heuristic
    inputs = [el for el in elements if el["is_input"]]
    buttons = [el for el in elements if el["is_button"]]
    links = [el for el in elements if el["is_link"]]
    headings = dom_meta.get("headings", [])

    page_type = "Content / Information Page"
    if "search" in url.lower() or any("search" in el["name"].lower() for el in inputs):
        page_type = "Search Results / Search Engine Page"
    elif len(inputs) >= 3 or dom_meta.get("formsCount", 0) > 0:
        page_type = "Interactive Form / Data-Entry Page"
    elif "jira" in url.lower() or "confluence" in url.lower():
        page_type = "Project Management / Jira Board"

    print("=" * 75)
    print(" 📄 ORCA BROWSER PAGE DESCRIPTION")
    print("=" * 75)
    print(f"📌 Page Title       : {title}")
    print(f"🌐 URL / Domain     : {domain} ({url[:65]}...)")
    print(f"🏷️  Page Category    : {page_type}")
    if dom_meta.get("metaDescription"):
        print(f"📝 Summary/Meta     : {dom_meta['metaDescription']}")
    print("-" * 75)

    print(f"📊 Elements Found   : {len(inputs)} Inputs/Fields | {len(buttons)} Buttons | {len(links)} Links")

    # 1. Available Input Fields
    print("\n⌨️  AVAILABLE INPUT FIELDS:")
    if not inputs:
        print("   (No interactive input fields detected on this page)")
    else:
        for i, inp in enumerate(inputs, 1):
            ref = inp["ref"]
            name = inp["name"] or "Unnamed input"
            role = inp["role"]
            print(f"   [{i}] Ref: [{ref}] | Type: {role:<10} | Label: \"{name}\"")
            print(f"       👉 Fill with: orca fill --element {ref} --value \"your text\"")

    # 2. Key Action Buttons
    print("\n🔘 KEY ACTION BUTTONS (Top 6):")
    if not buttons:
        print("   (No buttons detected)")
    else:
        for b in buttons[:6]:
            print(f"   - [{b['ref']}] \"{b['name']}\" (orca click --element {b['ref']})")

    # 3. Page Structure / Headings Outline
    if headings:
        print("\n📑 PAGE CONTENT OUTLINE / SECTIONS:")
        for h in headings[:8]:
            lvl = h["level"].upper()
            txt = h["text"]
            if len(txt) > 65:
                txt = txt[:62] + "..."
            print(f"   • [{lvl}] {txt}")

    print("=" * 75)

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

def search_elements(query: str, top_k: int = 5):
    snapshot = get_orca_snapshot()
    origin = snapshot.get("result", {}).get("origin", "unknown")
    total_refs = len(snapshot.get("result", {}).get("refs", {}))

    pruned = smart_dom_prune(snapshot)
    
    scored_elements = []
    for el in pruned:
        score = compute_semantic_score(query, el)
        if score > 0:
            scored_elements.append({**el, "score": score})

    scored_elements.sort(key=lambda x: x["score"], reverse=True)

    print(f"\n🔍 Querying Orca Active Page for: '{query}'...")
    print(f"🌐 Page Origin: {origin}")
    print(f"📊 DOM Pruning: Reduced from {total_refs} raw refs to {len(pruned)} clean semantic nodes.")
    print("=" * 70)

    if not scored_elements:
        print("❌ No matching elements found.")
        return

    for i, res in enumerate(scored_elements[:top_k], 1):
        ref = res["ref"]
        role = res["role"]
        name = res["name"] if len(res["name"]) <= 60 else res["name"][:57] + "..."
        score = res["score"]
        action = res["suggested_action"]

        print(f"#{i} [{res['ref']}] (Score: {score})")
        print(f"   Role: {role:<12} | Name: \"{name}\"")
        if action == "fill":
            print(f"   👉 Suggested Command: orca fill --element {ref} --value \"<text>\"")
        else:
            print(f"   👉 Suggested Command: orca click --element {ref}")
        print("-" * 70)

def main():
    # If no argument or "--describe" / "describe", run page description
    if len(sys.argv) < 2 or sys.argv[1].lower() in ["--describe", "describe", "-d", "info"]:
        describe_current_page()
    else:
        query = " ".join(sys.argv[1:])
        search_elements(query, top_k=5)

if __name__ == "__main__":
    main()
