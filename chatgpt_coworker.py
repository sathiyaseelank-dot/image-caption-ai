#!/usr/bin/env python3
"""
ChatGPT Coworker / Advisor / Orchestrator Agent CLI
===================================================
Automates interaction with ChatGPT in the active Orca browser tab:
- Discovers or creates a ChatGPT browser tab.
- Formulates prompts with specialized persona roles (Advisor, Orchestrator, Coworker).
- Injects and submits prompts directly into ChatGPT.
- Monitors generation/streaming until completion.
- Extracts clean markdown/text response to feed back into the development loop.

Usage:
  python3 chatgpt_coworker.py "How should we structure our database migration?"
  python3 chatgpt_coworker.py --role advisor "Review this architectural plan: ..."
  python3 chatgpt_coworker.py --role orchestrator "Break down this feature into sequential tasks: ..."
  python3 chatgpt_coworker.py --role coworker "Help me debug why this function returns null: ..."
  python3 chatgpt_coworker.py --prompt "..." --json
  python3 chatgpt_coworker.py --new-chat
"""

import sys
import os
import json
import time
import argparse
import subprocess
import re
from typing import Dict, Any, Optional, Tuple, List

ORCA_BIN = os.environ.get("ORCA_BIN", "/home/inkyank-01/.config/orca/linux-orca-cli-shim/orca")
CHATGPT_BASE_URL = "https://chatgpt.com/"

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

class CoworkerError(Exception):
    """Base exception for ChatGPT Coworker operations."""
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
        raise CoworkerError(f"Command timed out after {timeout}s: {' '.join(args)}")
    except Exception as e:
        raise CoworkerError(f"Subprocess execution failed: {e}")

    if res.returncode != 0:
        err = res.stderr.strip() or res.stdout.strip()
        raise CoworkerError(f"Command failed (exit code {res.returncode}): {err}")
    return res.stdout.strip()

def run_orca_eval(expression: str, page_id: Optional[str] = None, timeout: float = 20.0) -> str:
    """Evaluates JavaScript in the target Orca browser page."""
    cmd = [ORCA_BIN, "eval", "--expression", expression]
    if page_id:
        cmd.extend(["--page", page_id])
    return run_cmd(cmd, timeout=timeout)

def find_or_create_chatgpt_tab() -> Tuple[str, str]:
    """
    Finds an existing ChatGPT tab or launches a new one.
    Returns (browserPageId, url).
    """
    try:
        raw = run_cmd([ORCA_BIN, "tab", "list", "--json"], timeout=10.0)
        data = json.loads(raw)
        tabs = data.get("result", {}).get("tabs", [])
    except Exception as e:
        raise CoworkerError(f"Failed to query Orca tabs: {e}")

    for tab in tabs:
        url = tab.get("url", "")
        if "chatgpt.com" in url:
            page_id = tab.get("browserPageId")
            return page_id, url

    # Tab not found, create new tab
    print("🌐 No active ChatGPT tab found. Opening a new ChatGPT tab...")
    try:
        raw = run_cmd([ORCA_BIN, "tab", "create", "--url", CHATGPT_BASE_URL, "--json"], timeout=15.0)
        data = json.loads(raw)
        new_page_id = data.get("result", {}).get("browserPageId")
        if not new_page_id:
            raise CoworkerError("Created tab but did not receive browserPageId.")
        
        # Give the tab 3 seconds to initiate loading
        time.sleep(3.0)
        return new_page_id, CHATGPT_BASE_URL
    except Exception as e:
        raise CoworkerError(f"Failed to create ChatGPT tab: {e}")

def start_new_chat(page_id: str) -> bool:
    """Navigates the tab to a fresh new chat session."""
    nav_js = f"window.location.href = '{CHATGPT_BASE_URL}';"
    try:
        run_orca_eval(nav_js, page_id=page_id, timeout=10.0)
        time.sleep(2.5)
        return True
    except Exception as e:
        print(f"⚠️ Warning: Could not reset to new chat: {e}")
        return False

def count_assistant_messages(page_id: str) -> int:
    """Counts the existing assistant response messages currently in the DOM."""
    js = """
    (() => {
        const headings = Array.from(document.querySelectorAll("h1, h2, h3, h4, h5, h6"))
                             .filter(h => (h.innerText || '').includes("ChatGPT said:"));
        const articles = Array.from(document.querySelectorAll("[data-message-author-role='assistant'], .agent-turn"));
        return Math.max(headings.length, articles.length);
    })()
    """
    try:
        res = run_orca_eval(js, page_id=page_id, timeout=10.0)
        return int(res.strip())
    except Exception:
        return 0

def inject_and_send_prompt(prompt: str, page_id: str) -> Dict[str, Any]:
    """
    Injects prompt text into ChatGPT's composer textarea using React prototype setter
    and triggers submission.
    """
    escaped_prompt = json.dumps(prompt)
    js = f"""
    (() => {{
        const promptText = {escaped_prompt};
        const el = document.querySelector("#prompt-textarea") || 
                   document.querySelector("#mobile-composer-prompt") || 
                   document.querySelector("textarea") || 
                   document.querySelector("[contenteditable='true']");
        
        if (!el) return JSON.stringify({{ ok: false, error: "Prompt input element not found in DOM" }});

        el.focus();
        if (el.isContentEditable) {{
            el.innerText = promptText;
        }} else {{
            const proto = Object.getPrototypeOf(el);
            const desc = Object.getOwnPropertyDescriptor(proto, "value");
            if (desc && desc.set) {{
                desc.set.call(el, promptText);
            }} else {{
                el.value = promptText;
            }}
        }}

        el.dispatchEvent(new Event("input", {{ bubbles: true }}));
        el.dispatchEvent(new Event("change", {{ bubbles: true }}));

        // Search for active send button
        const sendBtn = document.querySelector("button[data-testid='send-button']") || 
                        document.querySelector("button[aria-label='Send message']") || 
                        document.querySelector("button[aria-label='Send prompt']") || 
                        document.querySelector("button[aria-label*='Send']");

        if (sendBtn && !sendBtn.disabled) {{
            sendBtn.click();
            return JSON.stringify({{ ok: true, method: "button_click" }});
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
        data = json.loads(res)
        return data
    except Exception as e:
        raise CoworkerError(f"Unexpected response from prompt injection: {res} ({e})")

def poll_for_response(initial_count: int, page_id: str, timeout: float = 120.0, verbose: bool = True) -> str:
    """
    Polls until ChatGPT finishes streaming its response and returns the clean text.
    """
    start_time = time.time()
    last_text = ""
    stable_count = 0
    poll_interval = 1.0

    if verbose:
        print("⏳ Waiting for ChatGPT to generate response...")

    time.sleep(1.5)  # Allow network dispatch and streaming to begin

    while time.time() - start_time < timeout:
        js = """
        (() => {
            const isStreaming = !!document.querySelector("button[data-testid='stop-button'], button[aria-label*='Stop'], .result-streaming");
            
            // Extract latest assistant message content
            const headings = Array.from(document.querySelectorAll("h1, h2, h3, h4, h5, h6"))
                                 .filter(h => (h.innerText || '').includes("ChatGPT said:"));
            let text = "";
            let hasNewMsg = false;
            
            if (headings.length > 0) {
                const lastHeading = headings[headings.length - 1];
                const sibling = lastHeading.nextElementSibling;
                if (sibling) {
                    text = (sibling.innerText || "").trim();
                } else if (lastHeading.parentElement) {
                    text = (lastHeading.parentElement.innerText || "").replace("ChatGPT said:", "").trim();
                }
                hasNewMsg = true;
            } else {
                const articles = Array.from(document.querySelectorAll("[data-message-author-role='assistant'], .agent-turn, article div.markdown"));
                if (articles.length > 0) {
                    text = (articles[articles.length - 1].innerText || "").trim();
                    hasNewMsg = true;
                }
            }

            const copyBtnExists = !!document.querySelector("button[aria-label*='Copy'], button[data-testid*='copy']");
            const totalCount = headings.length;

            return JSON.stringify({
                isStreaming: isStreaming,
                hasNewMsg: hasNewMsg,
                textLength: text.length,
                text: text,
                totalCount: totalCount,
                copyBtnExists: copyBtnExists
            });
        })()
        """
        try:
            raw = run_orca_eval(js, page_id=page_id, timeout=10.0)
            status = json.loads(raw)
        except Exception:
            time.sleep(poll_interval)
            continue

        is_streaming = status.get("isStreaming", False)
        current_text = status.get("text", "")
        has_new = status.get("hasNewMsg", False)
        total_count = status.get("totalCount", 0)

        # Check if generation has started
        if total_count > initial_count or has_new:
            if not is_streaming and len(current_text) > 0:
                # Text is present and stop button is gone
                if current_text == last_text:
                    stable_count += 1
                else:
                    stable_count = 0
                    last_text = current_text

                # When content has been stable for 2 consecutive polls without streaming
                if stable_count >= 2:
                    if verbose:
                        print(f"✅ Response received ({len(current_text)} characters).")
                    return current_text
            else:
                last_text = current_text
                stable_count = 0
                if verbose:
                    print(f"   ... streaming ({len(current_text)} chars generated)")

        time.sleep(poll_interval)

    if last_text:
        print("⚠️ Warning: Timeout reached, returning partially accumulated response.")
        return last_text
    raise CoworkerError(f"Timed out after {timeout} seconds waiting for ChatGPT response.")

def ask_chatgpt(
    prompt: str,
    role: str = "coworker",
    custom_role: Optional[str] = None,
    new_chat: bool = False,
    timeout: float = 120.0,
    verbose: bool = False
) -> Dict[str, Any]:
    """
    Main programmatic interface to query ChatGPT in Orca.
    """
    page_id, url = find_or_create_chatgpt_tab()
    
    if new_chat:
        if verbose:
            print("🔄 Starting new chat session...")
        start_new_chat(page_id)

    # Format prompt with persona role
    if custom_role:
        final_prompt = f"Role: {custom_role}\n\n{prompt}"
        role_label = "custom"
    else:
        role_prefix = ROLE_PROMPTS.get(role.lower(), "")
        final_prompt = f"{role_prefix}{prompt}".strip()
        role_label = role.lower()

    initial_turn_count = count_assistant_messages(page_id)

    if verbose:
        print(f"💬 Sending prompt [Role: {role_label.upper()}]...")

    inject_res = inject_and_send_prompt(final_prompt, page_id)
    if not inject_res.get("ok"):
        raise CoworkerError(f"Could not submit prompt: {inject_res.get('error')}")

    response_text = poll_for_response(initial_turn_count, page_id, timeout=timeout, verbose=verbose)

    return {
        "ok": True,
        "role": role_label,
        "page_id": page_id,
        "url": url,
        "prompt": prompt,
        "full_prompt_sent": final_prompt,
        "response": response_text
    }

def main():
    parser = argparse.ArgumentParser(
        description="ChatGPT Coworker / Advisor / Orchestrator Agent CLI"
    )
    parser.add_argument("prompt_words", nargs="*", help="Direct prompt text")
    parser.add_argument("-p", "--prompt", type=str, help="Full prompt string")
    parser.add_argument("-f", "--file", type=str, help="Read prompt from a file")
    parser.add_argument(
        "-r", "--role",
        type=str,
        default="coworker",
        choices=["advisor", "orchestrator", "coworker", "reviewer", "raw"],
        help="Persona role for the prompt (default: coworker)"
    )
    parser.add_argument("--custom-role", type=str, help="Custom role instruction prefix")
    parser.add_argument("--new-chat", action="store_true", help="Start a new chat session before prompting")
    parser.add_argument("--timeout", type=float, default=120.0, help="Response timeout in seconds (default: 120)")
    parser.add_argument("-j", "--json", action="store_true", help="Output results in strict JSON format")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress logs")

    args = parser.parse_args()

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
        if args.new_chat:
            page_id, _ = find_or_create_chatgpt_tab()
            start_new_chat(page_id)
            print("✅ Reset to new chat session.")
            sys.exit(0)
        parser.print_help()
        sys.exit(1)

    verbose = not args.quiet and not args.json

    try:
        res = ask_chatgpt(
            prompt=prompt,
            role=args.role,
            custom_role=args.custom_role,
            new_chat=args.new_chat,
            timeout=args.timeout,
            verbose=verbose
        )

        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 75)
            print(f" 🤖 CHATGPT {res['role'].upper()} RESPONSE")
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
