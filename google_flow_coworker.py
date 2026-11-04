#!/usr/bin/env python3
"""
Google Flow Autonomous Agent & Coworker Driver
=============================================
Automates driving Google Flow (flow.google.com) directly inside the Orca browser:
- Detects the active Google Flow tab and project metadata
- Inspects and tracks ongoing video/image generations and queue status
- Extracts generated media assets (video and image URLs, CDN keys, signatures)
- Reads full conversational history with thought blocks and agent turns
- Handles permission/credit confirmations (Approve / Always approve / Reject)
- Submits structured generation prompts and monitors fulfillment
- Emits structured JSON for agent workflows or runs interactively via CLI

Usage:
  python3 google_flow_coworker.py --status
  python3 google_flow_coworker.py --history
  python3 google_flow_coworker.py --media
  python3 google_flow_coworker.py --prompt "A majestic tiger walking through ancient ruins at sunset"
  python3 google_flow_coworker.py --approve
  python3 google_flow_coworker.py --status --json
"""

import sys
import os
import json
import time
import argparse
import subprocess
from typing import Dict, Any, Optional, List, Tuple

ORCA_BIN = os.environ.get("ORCA_BIN", "/home/inkyank-01/.config/orca/linux-orca-cli-shim/orca")


class GoogleFlowError(Exception):
    """Exception raised for Google Flow automation failures."""
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
        raise GoogleFlowError(f"Command timed out after {timeout}s: {' '.join(args)}")
    except Exception as e:
        raise GoogleFlowError(f"Execution failed: {e}")

    if res.returncode != 0:
        err = res.stderr.strip() or res.stdout.strip()
        raise GoogleFlowError(f"Command failed (exit code {res.returncode}): {err}")
    return res.stdout.strip()


def run_orca_eval(expression: str, page_id: Optional[str] = None, timeout: float = 15.0) -> str:
    """Evaluates JavaScript expression in Orca."""
    cmd = [ORCA_BIN, "eval", "--expression", expression]
    if page_id:
        cmd.extend(["--page", page_id])
    return run_cmd(cmd, timeout=timeout)


def get_orca_snapshot(page_id: Optional[str] = None) -> Dict[str, Any]:
    """Captures accessibility snapshot from Orca."""
    cmd = [ORCA_BIN, "snapshot", "--json"]
    if page_id:
        cmd.extend(["--page", page_id])
    raw = run_cmd(cmd, timeout=15.0)
    try:
        data = json.loads(raw)
        return data.get("result", {})
    except Exception as e:
        raise GoogleFlowError(f"Failed to parse Orca snapshot: {e}")


def find_google_flow_page() -> str:
    """Finds the open Google Flow tab in Orca or raises an error."""
    raw = run_cmd([ORCA_BIN, "tab", "list", "--json"], timeout=10.0)
    try:
        data = json.loads(raw)
        tabs = data.get("result", {}).get("tabs", [])
    except Exception as e:
        raise GoogleFlowError(f"Failed to list browser tabs: {e}")

    # Prioritize active tab if it's Google Flow
    for t in tabs:
        if "flow.google.com" in t.get("url", "") and t.get("active"):
            return t["browserPageId"]

    # Fallback to any tab with flow.google.com
    for t in tabs:
        if "flow.google.com" in t.get("url", ""):
            return t["browserPageId"]

    raise GoogleFlowError(
        "No Google Flow tab found in Orca. Please open https://flow.google.com in a browser tab."
    )


def get_project_status(page_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Extracts current Google Flow project state, active session, queue status,
    and available generation controls.
    """
    if not page_id:
        page_id = find_google_flow_page()

    js = """
    (() => {
        // Project title & session heading
        const projectTitle = document.querySelector('h1, [role="heading"][aria-level="1"], .project-title')?.innerText || '';
        const headings = Array.from(document.querySelectorAll('h2, h3, [role="heading"]')).map(h => (h.innerText || '').trim()).filter(Boolean);
        const sessionHeading = headings.length > 0 ? headings[0] : '';

        // Generation prompt editor
        const editor = document.querySelector('.ProseMirror, [contenteditable="true"]');
        const currentPrompt = editor ? (editor.innerText || '').trim() : '';

        // Start generation button
        const startBtn = Array.from(document.querySelectorAll('button')).find(b => {
            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
            const text = (b.innerText || '').toLowerCase();
            return aria.includes('start generation') || text.includes('start generation');
        });
        const isStartReady = startBtn ? (!startBtn.disabled && startBtn.getAttribute('aria-disabled') !== 'true') : false;

        // Pending approval radio options
        const activeApprovals = Array.from(document.querySelectorAll('[role="radio"], button')).filter(el => {
            const text = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
            const isApproveOrReject = text.includes('approve') || text.includes('reject');
            const isClickable = !el.hasAttribute('disabled') && el.getAttribute('aria-disabled') !== 'true';
            return isApproveOrReject && isClickable;
        }).map(el => ({
            tag: el.tagName,
            text: el.innerText.trim(),
            ariaLabel: el.getAttribute('aria-label') || ''
        }));

        // Queue & generation status indicators
        const agentRows = Array.from(document.querySelectorAll('.agent-row, .model-turn'));
        const lastAgentText = agentRows.length > 0 ? (agentRows[agentRows.length - 1].innerText || '') : '';
        const isQueued = lastAgentText.includes('waiting in the queue') || lastAgentText.includes('scheduled');
        const isGenerating = !!document.querySelector('.generating, mat-progress-bar, mat-spinner, [aria-label*="Generating"]');

        return JSON.stringify({
            url: window.location.href,
            pageTitle: document.title,
            projectTitle: projectTitle.trim(),
            sessionHeading: sessionHeading,
            currentPrompt: currentPrompt,
            isStartReady: isStartReady,
            isQueued: isQueued,
            isGenerating: isGenerating,
            pendingApprovals: activeApprovals,
            totalAgentTurns: agentRows.length
        });
    })()
    """
    raw = run_orca_eval(js, page_id=page_id)
    try:
        res = json.loads(raw)
        res["browserPageId"] = page_id
        return res
    except Exception as e:
        raise GoogleFlowError(f"Failed to extract project status: {e}")


def get_conversation_history(page_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieves the entire conversation history from the active Google Flow session,
    including user prompts, model planning, thinking traces, generated media links, and status.
    """
    if not page_id:
        page_id = find_google_flow_page()

    js = """
    (() => {
        const rows = Array.from(document.querySelectorAll('.message-row, .user-row, .agent-row'));
        const turns = [];

        rows.forEach((r, idx) => {
            const isUser = r.classList.contains('user-row');
            const isAgent = r.classList.contains('agent-row');
            if (!isUser && !isAgent) return;

            // Extract media thumbnails / assets in this row
            const mediaItems = Array.from(r.querySelectorAll('img, video, [role="button"][aria-label*="editor"]')).map(m => {
                const img = m.tagName === 'IMG' ? m : m.querySelector('img');
                const video = m.tagName === 'VIDEO' ? m : m.querySelector('video');
                return {
                    type: video ? 'video' : 'image',
                    ariaLabel: m.getAttribute('aria-label') || '',
                    src: (video ? video.src : (img ? img.src : '')) || m.src || ''
                };
            }).filter(item => item.src);

            // Clean up text
            let text = (r.innerText || '').trim();
            // Remove common icon button texts
            text = text.replace(/thumb_up\\s*thumb_down\\s*content_copy\\s*flag/g, '').trim();

            turns.push({
                turnIndex: idx,
                role: isUser ? 'user' : 'agent',
                text: text,
                media: mediaItems
            });
        });

        return JSON.stringify(turns);
    })()
    """
    raw = run_orca_eval(js, page_id=page_id)
    try:
        return json.loads(raw)
    except Exception as e:
        raise GoogleFlowError(f"Failed to extract conversation history: {e}")


def get_generated_media(page_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Extracts all generated video and image assets present in the session.
    """
    if not page_id:
        page_id = find_google_flow_page()

    js = """
    (() => {
        const mediaNodes = Array.from(document.querySelectorAll('img, video, [role="button"][aria-label*="editor"]'));
        const results = [];
        const seen = new Set();

        mediaNodes.forEach(node => {
            const img = node.tagName === 'IMG' ? node : node.querySelector('img');
            const video = node.tagName === 'VIDEO' ? node : node.querySelector('video');
            const src = (video ? video.src : (img ? img.src : '')) || node.src || '';
            const ariaLabel = node.getAttribute('aria-label') || (img ? img.getAttribute('aria-label') : '') || '';

            if (!src || seen.has(src)) return;
            if (src.includes('gstatic.com') || src.includes('googleusercontent.com/ogw')) return; // skip avatars/icons

            seen.add(src);
            const isVideo = !!video || src.includes('video') || ariaLabel.toLowerCase().includes('video');

            results.push({
                type: isVideo ? 'video' : 'image',
                ariaLabel: ariaLabel,
                src: src
            });
        });

        return JSON.stringify(results);
    })()
    """
    raw = run_orca_eval(js, page_id=page_id)
    try:
        return json.loads(raw)
    except Exception as e:
        raise GoogleFlowError(f"Failed to extract media items: {e}")


def handle_approval(decision: str = "approve", page_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Clicks an active confirmation prompt (e.g. Approve 10 credits generation).
    Decision can be: "approve", "always_approve", or "reject".
    """
    if not page_id:
        page_id = find_google_flow_page()

    decision_norm = decision.lower().replace("_", " ")

    js = f"""
    (() => {{
        const targetDecision = {json.dumps(decision_norm)};
        const candidates = Array.from(document.querySelectorAll('[role="radio"], button')).filter(el => {{
            const text = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
            const isClickable = !el.hasAttribute('disabled') && el.getAttribute('aria-disabled') !== 'true';
            return isClickable && (text.includes('approve') || text.includes('reject'));
        }});

        for (const el of candidates) {{
            const text = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
            if (text.includes(targetDecision)) {{
                el.click();
                return JSON.stringify({{ ok: true, clicked: text }});
            }}
        }}

        return JSON.stringify({{ ok: false, error: 'No active ' + targetDecision + ' option found' }});
    }})()
    """
    raw = run_orca_eval(js, page_id=page_id)
    try:
        return json.loads(raw)
    except Exception as e:
        raise GoogleFlowError(f"Failed to execute approval: {e}")


def send_prompt(prompt_text: str, auto_approve: bool = True, page_id: Optional[str] = None, wait_sec: float = 3.0) -> Dict[str, Any]:
    """
    Types prompt into the Google Flow prompt box and triggers generation.
    """
    if not page_id:
        page_id = find_google_flow_page()

    if not prompt_text.strip():
        raise GoogleFlowError("Prompt text cannot be empty.")

    # 1. Capture snapshot to find textbox ref
    snap = get_orca_snapshot(page_id=page_id)
    refs = snap.get("refs", {})

    target_ref = None
    clear_ref = None

    for ref_id, info in refs.items():
        name = (info.get("name") or "").lower()
        role = (info.get("role") or "").lower()
        if "what do you want to create" in name or role == "textbox":
            target_ref = ref_id
        if "clear prompt" in name:
            clear_ref = ref_id

    # If prompt box already contains text, clear it first
    if clear_ref:
        try:
            run_cmd([ORCA_BIN, "click", "--element", clear_ref, "--page", page_id, "--json"], timeout=10.0)
            time.sleep(0.3)
            # Re-snapshot to update refs
            snap = get_orca_snapshot(page_id=page_id)
            refs = snap.get("refs", {})
            for ref_id, info in refs.items():
                name = (info.get("name") or "").lower()
                if "what do you want to create" in name or info.get("role") == "textbox":
                    target_ref = ref_id
        except Exception:
            pass

    if not target_ref:
        raise GoogleFlowError("Could not locate Google Flow prompt input field [ref].")

    # 2. Fill prompt box using Orca CDP
    fill_cmd = [ORCA_BIN, "fill", "--element", target_ref, "--value", prompt_text, "--page", page_id, "--json"]
    run_cmd(fill_cmd, timeout=15.0)
    time.sleep(0.5)

    # 3. Locate and click "Start generation" button
    new_snap = get_orca_snapshot(page_id=page_id)
    start_ref = None
    for ref_id, info in new_snap.get("refs", {}).items():
        name = (info.get("name") or "").lower()
        role = (info.get("role") or "").lower()
        if "start generation" in name and role == "button":
            start_ref = ref_id
            break

    if not start_ref:
        # Fallback to DOM click if ref not matched in snapshot
        click_js = """
        (() => {
            const startBtn = Array.from(document.querySelectorAll('button')).find(b => {
                const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                const text = (b.innerText || '').toLowerCase();
                return aria.includes('start generation') || text.includes('start generation');
            });
            if (startBtn && !startBtn.disabled) {
                startBtn.click();
                return JSON.stringify({ ok: true });
            }
            return JSON.stringify({ ok: false, error: 'Start button not ready' });
        })()
        """
        raw = run_orca_eval(click_js, page_id=page_id)
        click_res = json.loads(raw)
        if not click_res.get("ok"):
            raise GoogleFlowError(f"Failed to trigger start generation: {click_res.get('error')}")
    else:
        click_cmd = [ORCA_BIN, "click", "--element", start_ref, "--page", page_id, "--json"]
        run_cmd(click_cmd, timeout=15.0)

    time.sleep(wait_sec)

    # 4. If auto_approve is set, check if credit approval modal or radio prompt immediately popped up
    approved_action = None
    if auto_approve:
        time.sleep(1.0)
        app_res = handle_approval("approve", page_id=page_id)
        if app_res.get("ok"):
            approved_action = app_res

    status = get_project_status(page_id=page_id)
    return {
        "ok": True,
        "action": "prompt_submitted",
        "prompt": prompt_text,
        "auto_approved": approved_action,
        "status": status
    }


def main():
    parser = argparse.ArgumentParser(
        description="Google Flow Autonomous Agent & Coworker Driver"
    )
    parser.add_argument("-s", "--status", action="store_true", help="Display project & generation status")
    parser.add_argument("-H", "--history", action="store_true", help="Display session turn history")
    parser.add_argument("-m", "--media", action="store_true", help="List all generated media assets (video & images)")
    parser.add_argument("-p", "--prompt", type=str, help="Submit a new generation prompt to Google Flow")
    parser.add_argument("--approve", action="store_true", help="Approve pending generation/credit request")
    parser.add_argument("--reject", action="store_true", help="Reject pending generation/credit request")
    parser.add_argument("--always-approve", action="store_true", help="Always approve generation requests")
    parser.add_argument("--page", type=str, help="Specific browser page ID")
    parser.add_argument("-j", "--json", action="store_true", help="Emit JSON output")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress decorative banners")

    args = parser.parse_args()

    try:
        page_id = args.page or find_google_flow_page()

        if args.approve or args.always_approve or args.reject:
            decision = "always approve" if args.always_approve else ("reject" if args.reject else "approve")
            res = handle_approval(decision=decision, page_id=page_id)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                if res.get("ok"):
                    print(f"✅ Clicked approval action: {res.get('clicked')}")
                else:
                    print(f"ℹ️ {res.get('error')}")
            return

        if args.prompt:
            if not args.quiet and not args.json:
                print("=" * 75)
                print(" 🚀 SUBMITTING PROMPT TO GOOGLE FLOW")
                print("=" * 75)
                print(f"Prompt: {args.prompt}")
                print("-" * 75)

            res = send_prompt(args.prompt, page_id=page_id)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print("✅ Prompt dispatched successfully.")
                if res.get("auto_approved"):
                    print("✅ Automatically approved credit generation prompt.")
                stat = res.get("status", {})
                if stat.get("isQueued"):
                    print("⏳ Generation is queued/scheduled on Google Flow servers.")
            return

        if args.history:
            history = get_conversation_history(page_id=page_id)
            if args.json:
                print(json.dumps(history, indent=2))
            else:
                print("=" * 75)
                print(f" 📜 GOOGLE FLOW SESSION HISTORY ({len(history)} turns)")
                print("=" * 75)
                for t in history:
                    role_icon = "👤 USER" if t["role"] == "user" else "🤖 AGENT"
                    print(f"[{t['turnIndex']}] {role_icon}:")
                    text_snip = t["text"][:300] + ("..." if len(t["text"]) > 300 else "")
                    print(f"    {text_snip}")
                    if t.get("media"):
                        for m in t["media"]:
                            print(f"    📸 [{m['type'].upper()}]: {m['src'][:90]}...")
                    print("-" * 75)
            return

        if args.media:
            media = get_generated_media(page_id=page_id)
            if args.json:
                print(json.dumps(media, indent=2))
            else:
                print("=" * 75)
                print(f" 🎨 GENERATED ASSETS IN GOOGLE FLOW ({len(media)} items)")
                print("=" * 75)
                for i, m in enumerate(media, 1):
                    icon = "🎬" if m["type"] == "video" else "🖼️"
                    label = f" ({m['ariaLabel']})" if m.get("ariaLabel") else ""
                    print(f"[{i}] {icon} {m['type'].upper()}{label}")
                    print(f"    URL: {m['src']}")
            return

        # Default action: status
        status = get_project_status(page_id=page_id)
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            print("=" * 75)
            print(" 🌟 GOOGLE FLOW COWORKER STATUS")
            print("=" * 75)
            print(f"Project Name      : {status.get('projectTitle') or 'Unnamed'}")
            print(f"Session Heading   : {status.get('sessionHeading') or 'Default'}")
            print(f"Page ID           : {status.get('browserPageId')}")
            print(f"Start Button Ready: {'Yes' if status.get('isStartReady') else 'No (Prompt empty or disabled)'}")
            print(f"Queue Status      : {'Waiting in queue / scheduled' if status.get('isQueued') else 'Idle / Complete'}")
            print(f"Active Approvals  : {len(status.get('pendingApprovals', []))} pending")
            print(f"Total Agent Turns : {status.get('totalAgentTurns')}")
            print("=" * 75)

    except GoogleFlowError as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        else:
            print(f"\n❌ Google Flow Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
