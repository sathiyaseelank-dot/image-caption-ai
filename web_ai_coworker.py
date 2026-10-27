#!/usr/bin/env python3
"""
Universal Web AI Coworker / Advisor / Orchestrator Agent CLI
============================================================
A multi-platform automation script that interfaces with ANY conversational Web AI
currently open in the Orca browser:
  - ChatGPT (chatgpt.com)
  - Claude (claude.ai)
  - Google Gemini (gemini.google.com)
  - Perplexity (perplexity.ai)
  - DeepSeek (chat.deepseek.com)
  - Microsoft Copilot (copilot.microsoft.com)
  - Generic / Custom Web AI interfaces

Features:
- Auto-detects open Web AI tabs or launches a requested AI platform.
- Injects prompts using native prototype setters (both <textarea> and contenteditable/ProseMirror/Quill).
- Automatically triggers submission (button click or Enter keypress).
- Monitors streaming state across platforms and polls until generation completes.
- Extracts clean markdown/text responses without UI buttons or headers.
- Supports persona roles: Advisor, Orchestrator, Coworker, Reviewer, or Raw.
- Full CLI (human-formatted or strict --json) and Python importable module.

Usage:
  python3 web_ai_coworker.py "How should we design this service?"
  python3 web_ai_coworker.py --ai claude --role advisor "Review this architecture"
  python3 web_ai_coworker.py --ai gemini --role orchestrator "Break down this project"
  python3 web_ai_coworker.py --ai perplexity "Find latest benchmarks for..."
  python3 web_ai_coworker.py --list-ais
  python3 web_ai_coworker.py --prompt "..." --json
"""

import sys
import os
import json
import time
import argparse
import subprocess
from typing import Dict, Any, Optional, Tuple, List

ORCA_BIN = os.environ.get("ORCA_BIN", "/home/inkyank-01/.config/orca/linux-orca-cli-shim/orca")

# Registered Web AI Provider Configurations
AI_PROVIDERS = {
    "chatgpt": {
        "name": "ChatGPT",
        "url_match": "chatgpt.com",
        "default_url": "https://chatgpt.com/",
        "composer_selectors": [
            "#prompt-textarea",
            "#mobile-composer-prompt",
            "textarea",
            "[contenteditable='true']"
        ],
        "send_selectors": [
            "button[data-testid='send-button']",
            "button[aria-label='Send message']",
            "button[aria-label='Send prompt']",
            "button[aria-label*='Send']"
        ],
        "stop_selectors": [
            "button[data-testid='stop-button']",
            "button[aria-label*='Stop']",
            ".result-streaming"
        ],
        "response_selectors": [
            "main [role='listitem']",
            "[data-message-author-role='assistant']",
            ".agent-turn",
            "article div.markdown"
        ],
        "special_heading": "ChatGPT said:"
    },
    "claude": {
        "name": "Claude",
        "url_match": "claude.ai",
        "default_url": "https://claude.ai/new",
        "composer_selectors": [
            "div.ProseMirror[contenteditable='true']",
            "fieldset [contenteditable='true']",
            "div[contenteditable='true']",
            "textarea"
        ],
        "send_selectors": [
            "button[aria-label='Send Message']",
            "button[aria-label='Send prompt']",
            "button[data-testid='send-button']",
            "button[aria-label*='Send']"
        ],
        "stop_selectors": [
            "button[aria-label='Stop Response']",
            "button[aria-label*='Stop']",
            "[data-is-streaming='true']"
        ],
        "response_selectors": [
            ".font-claude-message",
            "[data-is-streaming='false']",
            "[data-testid='chat-message-assistant']",
            "div.standard-markdown"
        ],
        "special_heading": None
    },
    "gemini": {
        "name": "Google Gemini",
        "url_match": "gemini.google.com",
        "default_url": "https://gemini.google.com/app",
        "composer_selectors": [
            "rich-textarea .ql-editor[contenteditable='true']",
            ".textarea[contenteditable='true']",
            "div[contenteditable='true']",
            "textarea"
        ],
        "send_selectors": [
            "button[aria-label*='Send prompt']",
            "button[aria-label*='Send message']",
            "button.send-button",
            "button[aria-label*='Send']"
        ],
        "stop_selectors": [
            "button[aria-label*='Stop response']",
            "button[aria-label*='Stop']",
            ".generating-response"
        ],
        "response_selectors": [
            "message-content",
            "model-response",
            ".response-container",
            ".model-content"
        ],
        "special_heading": None
    },
    "perplexity": {
        "name": "Perplexity",
        "url_match": "perplexity.ai",
        "default_url": "https://www.perplexity.ai/",
        "composer_selectors": [
            "textarea[placeholder*='Ask']",
            "textarea",
            "div[contenteditable='true']"
        ],
        "send_selectors": [
            "button[aria-label='Submit']",
            "button[aria-label='Send']",
            "button[type='submit']"
        ],
        "stop_selectors": [
            "button[aria-label='Stop']",
            ".animate-pulse"
        ],
        "response_selectors": [
            "[data-testid='answer-content']",
            ".prose",
            ".default.font-sans"
        ],
        "special_heading": None
    },
    "deepseek": {
        "name": "DeepSeek",
        "url_match": "chat.deepseek.com",
        "default_url": "https://chat.deepseek.com/",
        "composer_selectors": [
            "textarea[placeholder*='Send']",
            "textarea#chat-input",
            "textarea"
        ],
        "send_selectors": [
            "div[role='button'][aria-label*='Send']",
            "button[aria-label*='Send']",
            "div[class*='send-button']"
        ],
        "stop_selectors": [
            ".ds-stop-button",
            "button[aria-label*='Stop']"
        ],
        "response_selectors": [
            "div.ds-markdown",
            ".ds-message-assistant"
        ],
        "special_heading": None
    },
    "copilot": {
        "name": "Microsoft Copilot",
        "url_match": "copilot.microsoft.com",
        "default_url": "https://copilot.microsoft.com/",
        "composer_selectors": [
            "#userInput",
            "textarea[placeholder*='Ask']",
            "textarea"
        ],
        "send_selectors": [
            "button[aria-label='Submit']",
            "button[aria-label='Send']",
            "button[type='submit']"
        ],
        "stop_selectors": [
            "button[aria-label='Stop Generating']",
            ".stop-button"
        ],
        "response_selectors": [
            "cib-message-group[source='bot']",
            ".ac-textBlock",
            "div[role='region']"
        ],
        "special_heading": None
    },
    "zai": {
        "name": "Z.ai",
        "url_match": "chat.z.ai",
        "default_url": "https://chat.z.ai/",
        "composer_selectors": [
            "#chat-input",
            "textarea.input-scroll",
            "textarea[placeholder*='Send']",
            "textarea"
        ],
        "send_selectors": [
            "#send-message-button",
            "button.sendMessageButton",
            "button[aria-label*='Send']",
            "button:has(svg)"
        ],
        "stop_selectors": [
            "button[aria-label*='Stop']",
            "[aria-label*='Stop']",
            ".sendMessageButton.stop"
        ],
        "response_selectors": [
            "#response-content-container",
            ".chat-assistant",
            "div[class*='message-']:not(.user-message)"
        ],
        "special_heading": None,
        "clean_selectors": [
            ".thinking-chain-container",
            "[class*='thinking']"
        ]
    }
}

ROLE_PROMPTS = {
    "advisor": (
        "You are acting as an expert Technical Advisor and Principal Architect. "
        "Review the following context, evaluate trade-offs, identify edge cases and potential pitfalls, "
        "and provide strategic, clear, actionable recommendations:\n\n"
    ),
    "orchestrator": (
        "You are acting as a Master Task Orchestrator. "
        "Analyze the following goal/requirements, break them down into logically sequenced, prioritized, "
        "and actionable execution steps, and highlight dependencies and verification criteria:\n\n"
    ),
    "coworker": (
        "You are acting as a Senior Pair-Programming Coworker. "
        "Collaborate with me on this problem. Be concise, direct, brainstorm effective solutions, "
        "and provide clean, idiomatic code examples with explanations where needed:\n\n"
    ),
    "reviewer": (
        "You are acting as a Rigorous Code and Plan Reviewer. "
        "Critique the following implementation/design for correctness, performance, maintainability, "
        "security, and edge-case resilience:\n\n"
    ),
    "raw": ""
}

class UniversalAIError(Exception):
    """Base exception for Universal Web AI operations."""
    pass

def run_cmd(args: List[str], timeout: float = 30.0) -> str:
    """Executes a command and returns its stdout."""
    try:
        res = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise UniversalAIError(f"Command timed out after {timeout}s: {' '.join(args)}")
    except Exception as e:
        raise UniversalAIError(f"Subprocess execution failed: {e}")

    if res.returncode != 0:
        err = res.stderr.strip() or res.stdout.strip()
        raise UniversalAIError(f"Command failed (exit code {res.returncode}): {err}")
    return res.stdout.strip()

def run_orca_eval(expression: str, page_id: Optional[str] = None, timeout: float = 20.0) -> str:
    """Evaluates JavaScript in the target Orca browser page."""
    cmd = [ORCA_BIN, "eval", "--expression", expression]
    if page_id:
        cmd.extend(["--page", page_id])
    return run_cmd(cmd, timeout=timeout)

def list_open_ai_tabs() -> List[Dict[str, Any]]:
    """Returns all open browser tabs matching known Web AI providers."""
    try:
        raw = run_cmd([ORCA_BIN, "tab", "list", "--json"], timeout=10.0)
        data = json.loads(raw)
        tabs = data.get("result", {}).get("tabs", [])
    except Exception as e:
        raise UniversalAIError(f"Failed to query Orca tabs: {e}")

    found = []
    for tab in tabs:
        url = tab.get("url", "")
        for p_key, cfg in AI_PROVIDERS.items():
            if cfg["url_match"] in url:
                found.append({
                    "provider": p_key,
                    "name": cfg["name"],
                    "browserPageId": tab.get("browserPageId"),
                    "url": url,
                    "title": tab.get("title", ""),
                    "active": tab.get("active", False)
                })
                break
    return found

def resolve_target_tab(requested_provider: Optional[str] = None) -> Tuple[str, str, Dict[str, Any]]:
    """
    Finds or launches the appropriate Web AI tab.
    Returns (browserPageId, current_url, provider_config).
    """
    open_ais = list_open_ai_tabs()

    if requested_provider and requested_provider.lower() != "auto":
        target_key = requested_provider.lower()
        if target_key not in AI_PROVIDERS:
            supported = ", ".join(AI_PROVIDERS.keys())
            raise UniversalAIError(f"Unknown provider '{requested_provider}'. Supported: {supported}")

        cfg = AI_PROVIDERS[target_key]
        # Check if tab is already open
        for tab in open_ais:
            if tab["provider"] == target_key:
                return tab["browserPageId"], tab["url"], cfg

        # Open requested tab
        print(f"🌐 Opening new {cfg['name']} tab ({cfg['default_url']})...")
        raw = run_cmd([ORCA_BIN, "tab", "create", "--url", cfg["default_url"], "--json"], timeout=15.0)
        data = json.loads(raw)
        new_page_id = data.get("result", {}).get("browserPageId")
        time.sleep(3.5)
        return new_page_id, cfg["default_url"], cfg

    # Auto-detection mode:
    # 1. Prefer currently active AI tab if any
    for tab in open_ais:
        if tab.get("active"):
            return tab["browserPageId"], tab["url"], AI_PROVIDERS[tab["provider"]]

    # 2. Otherwise pick first available open AI tab
    if open_ais:
        first = open_ais[0]
        return first["browserPageId"], first["url"], AI_PROVIDERS[first["provider"]]

    # 3. Default fallback: Open ChatGPT tab
    default_cfg = AI_PROVIDERS["chatgpt"]
    print(f"🌐 No open AI tabs found. Launching default provider: {default_cfg['name']}...")
    raw = run_cmd([ORCA_BIN, "tab", "create", "--url", default_cfg["default_url"], "--json"], timeout=15.0)
    data = json.loads(raw)
    new_page_id = data.get("result", {}).get("browserPageId")
    time.sleep(3.5)
    return new_page_id, default_cfg["default_url"], default_cfg

def inject_and_submit_prompt(prompt: str, page_id: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Injects prompt text into the AI composer (supporting both <textarea> and contenteditable/ProseMirror/Quill)
    and clicks send or dispatches Enter.
    """
    payload = {
        "prompt": prompt,
        "composerSelectors": cfg["composer_selectors"],
        "sendSelectors": cfg["send_selectors"]
    }
    encoded = json.dumps(payload)

    js = f"""
    (() => {{
        const config = {encoded};
        const promptText = config.prompt;

        // 1. Locate composer element
        let el = null;
        for (const sel of config.composerSelectors) {{
            const found = document.querySelector(sel);
            if (found && (found.offsetParent !== null || found.isContentEditable || found.tagName === 'TEXTAREA')) {{
                el = found;
                break;
            }}
        }}

        if (!el) {{
            el = document.querySelector("textarea, [contenteditable='true']");
        }}

        if (!el) return JSON.stringify({{ ok: false, error: "Composer input element not found in DOM" }});

        el.focus();

        // 2. Insert text based on element type
        if (el.isContentEditable || el.classList.contains('ProseMirror') || el.classList.contains('ql-editor')) {{
            // ContentEditable / Rich Text (Claude, Gemini)
            el.focus();
            // Clear existing content
            el.innerHTML = '';
            const p = document.createElement('p');
            p.textContent = promptText;
            el.appendChild(p);

            // Dispatch input and selection events
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            el.dispatchEvent(new InputEvent('input', {{
                bubbles: true,
                cancelable: true,
                inputType: 'insertText',
                data: promptText
            }}));
        }} else {{
            // Standard Textarea (ChatGPT, Perplexity, DeepSeek, Copilot, Z.ai)
            const proto = Object.getPrototypeOf(el);
            const desc = Object.getOwnPropertyDescriptor(proto, "value");
            if (desc && desc.set) {{
                desc.set.call(el, promptText);
            }} else {{
                el.value = promptText;
            }}
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            try {{
                el.dispatchEvent(new InputEvent('input', {{
                    bubbles: true,
                    cancelable: true,
                    inputType: 'insertText',
                    data: promptText
                }}));
            }} catch(e) {{}}
            el.dispatchEvent(new KeyboardEvent('keydown', {{ bubbles: true }}));
            el.dispatchEvent(new KeyboardEvent('keyup', {{ bubbles: true }}));
        }}

        // 3. Trigger Submission
        let sendBtn = null;
        for (const sel of config.sendSelectors) {{
            const btn = document.querySelector(sel);
            if (btn) {{
                sendBtn = btn;
                break;
            }}
        }}

        if (sendBtn) {{
            sendBtn.disabled = false;
            sendBtn.click();
            return JSON.stringify({{ ok: true, method: "button_click", buttonSelector: sendBtn.tagName }});
        }}

        // Fallback: Dispatch Enter key event
        const enterEvt = new KeyboardEvent("keydown", {{
            key: "Enter",
            code: "Enter",
            keyCode: 13,
            which: 13,
            bubbles: true,
            cancelable: true
        }});
        el.dispatchEvent(enterEvt);
        return JSON.stringify({{ ok: true, method: "enter_keypress" }});
    }})()
    """
    res = run_orca_eval(js, page_id=page_id, timeout=15.0)
    try:
        return json.loads(res)
    except Exception as e:
        raise UniversalAIError(f"Unexpected response from prompt injection: {res} ({e})")

def get_current_ai_response_text(page_id: str, cfg: Dict[str, Any]) -> str:
    """Retrieves the currently visible assistant response text before submitting a new prompt."""
    payload = {
        "responseSelectors": cfg["response_selectors"],
        "specialHeading": cfg["special_heading"],
        "cleanSelectors": cfg.get("clean_selectors", [])
    }
    encoded = json.dumps(payload)
    js = f"""
    (() => {{
        const config = {encoded};
        let text = "";
        if (config.specialHeading) {{
            const headings = Array.from(document.querySelectorAll("h1, h2, h3, h4, h5, h6"))
                                 .filter(h => (h.innerText || '').includes(config.specialHeading));
            if (headings.length > 0) {{
                const lastHeading = headings[headings.length - 1];
                const sibling = lastHeading.nextElementSibling;
                if (sibling) text = (sibling.innerText || "").trim();
                else if (lastHeading.parentElement) {{
                    text = (lastHeading.parentElement.innerText || "").replace(config.specialHeading, "").trim();
                }}
            }}
        }}

        if (!text) {{
            for (const sel of config.responseSelectors) {{
                const elements = Array.from(document.querySelectorAll(sel));
                if (elements.length > 0) {{
                    const last = elements[elements.length - 1];
                    const clone = last.cloneNode(true);
                    if (config.cleanSelectors) {{
                        config.cleanSelectors.forEach(cs => {{
                            clone.querySelectorAll(cs).forEach(el => el.remove());
                        }});
                    }}
                    clone.querySelectorAll('button, svg').forEach(el => el.remove());
                    text = (clone.innerText || "").trim();
                    if (text.length > 0) break;
                }}
            }}
        }}
        return text;
    }})()
    """
    try:
        return run_orca_eval(js, page_id=page_id, timeout=10.0).strip()
    except Exception:
        return ""

def poll_for_ai_response(
    page_id: str,
    cfg: Dict[str, Any],
    initial_text: str = "",
    timeout: float = 120.0,
    verbose: bool = True
) -> str:
    """
    Polls until the Web AI finishes streaming its response and returns the clean text.
    """
    start_time = time.time()
    last_text = ""
    stable_count = 0
    poll_interval = 1.0

    if verbose:
        print(f"⏳ Waiting for {cfg['name']} to generate response...")

    time.sleep(2.0)  # Initial latency for streaming to begin

    payload = {
        "stopSelectors": cfg["stop_selectors"],
        "responseSelectors": cfg["response_selectors"],
        "specialHeading": cfg["special_heading"],
        "cleanSelectors": cfg.get("clean_selectors", [])
    }
    encoded = json.dumps(payload)

    while time.time() - start_time < timeout:
        js = f"""
        (() => {{
            const config = {encoded};

            // 1. Detect if response is currently streaming
            let isStreaming = false;
            for (const sel of config.stopSelectors) {{
                const el = document.querySelector(sel);
                if (el && (el.offsetParent !== null || el.offsetWidth > 0 || el.offsetHeight > 0)) {{
                    isStreaming = true;
                    break;
                }}
            }}

            // 2. Extract latest assistant response text
            let text = "";

            if (config.specialHeading) {{
                const headings = Array.from(document.querySelectorAll("h1, h2, h3, h4, h5, h6"))
                                     .filter(h => (h.innerText || '').includes(config.specialHeading));
                if (headings.length > 0) {{
                    const lastHeading = headings[headings.length - 1];
                    const sibling = lastHeading.nextElementSibling;
                    if (sibling) text = (sibling.innerText || "").trim();
                    else if (lastHeading.parentElement) {{
                        text = (lastHeading.parentElement.innerText || "").replace(config.specialHeading, "").trim();
                    }}
                }}
            }}

            if (!text) {{
                for (const sel of config.responseSelectors) {{
                    const elements = Array.from(document.querySelectorAll(sel));
                    if (elements.length > 0) {{
                        const last = elements[elements.length - 1];
                        const clone = last.cloneNode(true);
                        if (config.cleanSelectors) {{
                            config.cleanSelectors.forEach(cs => {{
                                clone.querySelectorAll(cs).forEach(el => el.remove());
                            }});
                        }}
                        clone.querySelectorAll('button, svg').forEach(el => el.remove());
                        text = (clone.innerText || "").trim();
                        if (text.length > 0) break;
                    }}
                }}
            }}

            // Clean trailing action buttons if any (e.g. "Copy response", "Retry")
            text = text.replace(/\\n*(Copy|Share|Retry|Edit|Good response|Bad response)\\s*$/i, "").trim();

            return JSON.stringify({{
                isStreaming: isStreaming,
                textLength: text.length,
                text: text
            }});
        }})()
        """
        try:
            raw = run_orca_eval(js, page_id=page_id, timeout=10.0)
            status = json.loads(raw)
        except Exception:
            time.sleep(poll_interval)
            continue

        is_streaming = status.get("isStreaming", False)
        current_text = status.get("text", "")

        if len(current_text) > 0 and current_text != initial_text:
            if not is_streaming:
                if current_text == last_text:
                    stable_count += 1
                else:
                    stable_count = 0
                    last_text = current_text

                # Content stable across 2 consecutive checks without active stop indicator
                if stable_count >= 2:
                    if verbose:
                        print(f"✅ {cfg['name']} response completed ({len(current_text)} characters).")
                    return current_text
            else:
                last_text = current_text
                stable_count = 0
                if verbose:
                    print(f"   ... streaming from {cfg['name']} ({len(current_text)} chars generated)")

        time.sleep(poll_interval)

    if last_text and last_text != initial_text:
        print(f"⚠️ Warning: Timeout reached, returning partially accumulated response from {cfg['name']}.")
        return last_text
    raise UniversalAIError(f"Timed out after {timeout} seconds waiting for {cfg['name']} response.")

def ask_web_ai(
    prompt: str,
    provider: Optional[str] = "auto",
    role: str = "coworker",
    custom_role: Optional[str] = None,
    timeout: float = 120.0,
    verbose: bool = False
) -> Dict[str, Any]:
    """
    Main programmatic interface to query any Web AI in Orca.
    """
    page_id, url, cfg = resolve_target_tab(provider)

    # Format prompt with persona role
    if custom_role:
        final_prompt = f"Role: {custom_role}\n\n{prompt}"
        role_label = "custom"
    else:
        role_prefix = ROLE_PROMPTS.get(role.lower(), "")
        final_prompt = f"{role_prefix}{prompt}".strip()
        role_label = role.lower()

    if verbose:
        print(f"💬 Sending prompt to [{cfg['name'].upper()}] [Role: {role_label.upper()}]...")

    initial_text = get_current_ai_response_text(page_id, cfg)

    inject_res = inject_and_submit_prompt(final_prompt, page_id, cfg)
    if not inject_res.get("ok"):
        raise UniversalAIError(f"Failed to submit prompt: {inject_res.get('error')}")

    response_text = poll_for_ai_response(page_id, cfg, initial_text=initial_text, timeout=timeout, verbose=verbose)

    return {
        "ok": True,
        "provider": cfg["name"].lower().replace(" ", "_"),
        "provider_name": cfg["name"],
        "role": role_label,
        "page_id": page_id,
        "url": url,
        "prompt": prompt,
        "full_prompt_sent": final_prompt,
        "response": response_text
    }

def main():
    parser = argparse.ArgumentParser(
        description="Universal Web AI Coworker / Advisor / Orchestrator Agent CLI"
    )
    parser.add_argument("prompt_words", nargs="*", help="Direct prompt text")
    parser.add_argument("-p", "--prompt", type=str, help="Full prompt string")
    parser.add_argument("-f", "--file", type=str, help="Read prompt from a file")
    parser.add_argument(
        "--ai",
        type=str,
        default="auto",
        help="Target Web AI platform: auto, chatgpt, claude, gemini, perplexity, deepseek, copilot (default: auto)"
    )
    parser.add_argument(
        "-r", "--role",
        type=str,
        default="coworker",
        choices=["advisor", "orchestrator", "coworker", "reviewer", "raw"],
        help="Persona role for the prompt (default: coworker)"
    )
    parser.add_argument("--custom-role", type=str, help="Custom role instruction prefix")
    parser.add_argument("--list-ais", action="store_true", help="List all currently open Web AI tabs")
    parser.add_argument("--timeout", type=float, default=120.0, help="Response timeout in seconds (default: 120)")
    parser.add_argument("-j", "--json", action="store_true", help="Output results in strict JSON format")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress logs")

    args = parser.parse_args()

    if args.list_ais:
        tabs = list_open_ai_tabs()
        if args.json:
            print(json.dumps({"ok": True, "open_ai_tabs": tabs}, indent=2))
        else:
            print("\n" + "=" * 75)
            print(" 🌐 OPEN WEB AI BROWSER TABS")
            print("=" * 75)
            if not tabs:
                print("   (No active Web AI tabs found. Run with --ai <platform> to open one)")
            else:
                for i, t in enumerate(tabs, 1):
                    act = " [ACTIVE]" if t["active"] else ""
                    print(f"   [{i}] {t['name']:<15} | Page ID: {t['browserPageId']}{act}")
                    print(f"       URL: {t['url']}")
            print("=" * 75)
        sys.exit(0)

    # Determine prompt text
    prompt = None
    if args.file:
        if not os.path.exists(args.file):
            print(f"Error: Prompt file '{args.file}' not found.", file=sys.stderr)
            sys.exit(1)
        with open(args.file, "r", encoding="utf-8") as f:
            prompt = f.read().strip()
    elif args.prompt:
        prompt = args.prompt.strip()
    elif args.prompt_words:
        prompt = " ".join(args.prompt_words).strip()
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()

    if not prompt:
        parser.print_help()
        sys.exit(1)

    verbose = not args.quiet and not args.json

    try:
        res = ask_web_ai(
            prompt=prompt,
            provider=args.ai,
            role=args.role,
            custom_role=args.custom_role,
            timeout=args.timeout,
            verbose=verbose
        )

        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 75)
            print(f" 🤖 {res['provider_name'].upper()} {res['role'].upper()} RESPONSE")
            print("=" * 75)
            print(res["response"])
            print("=" * 75)

    except Exception as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        else:
            print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
