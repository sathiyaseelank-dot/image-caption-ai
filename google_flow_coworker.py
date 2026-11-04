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


def navigate_sidebar_tab(tab_name: str, page_id: Optional[str] = None) -> Dict[str, Any]:
    """Navigates to a specific sidebar section (e.g. Characters, All media, Images, Videos, Scenes, Uploads)."""
    page_id = page_id or find_google_flow_page()
    js_code = f"""(() => {{
        const target = {json.dumps(tab_name)}.toLowerCase().trim();
        const drawer = document.querySelector(".mat-drawer-inner-container");
        if (!drawer) return {{ ok: false, error: "Sidebar drawer not found" }};
        
        const items = Array.from(drawer.querySelectorAll("button, a, [role='button'], mat-list-item"));
        const match = items.find(el => (el.innerText || "").toLowerCase().includes(target));
        if (!match) {{
            const available = items.map(i => (i.innerText || "").replace(/\\n+/g, " ").trim()).filter(Boolean);
            return {{ ok: false, error: "Tab not found: " + target, available }};
        }}
        
        match.click();
        return {{
            ok: true,
            tab: target,
            activeText: (match.innerText || "").replace(/\\n+/g, " ").trim(),
            url: window.location.href
        }};
    }})()"""
    raw = run_orca_eval(js_code, page_id=page_id)
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": True, "raw": raw}


def get_characters_info(page_id: Optional[str] = None) -> Dict[str, Any]:
    """Navigates to Characters tab and retrieves all existing characters and action tiles."""
    page_id = page_id or find_google_flow_page()
    nav_res = navigate_sidebar_tab("Characters", page_id=page_id)
    if not nav_res.get("ok"):
        return nav_res

    time.sleep(0.5)

    js_code = """(() => {
        const tiles = Array.from(document.querySelectorAll(".character-tile-container, [class*='character-tile']"));
        const characters = [];
        const seen = new Set();
        for (const t of tiles) {
            const nameEl = t.querySelector(".character-tile-name, [class*='name']");
            const imgEl = t.querySelector("img");
            const name = nameEl ? nameEl.innerText.trim() : t.innerText.trim();
            if (name && !seen.has(name)) {
                seen.add(name);
                characters.push({
                    name: name,
                    thumbnail: imgEl ? (imgEl.src || null) : null
                });
            }
        }
        
        const actionTiles = Array.from(document.querySelectorAll("button.custom-tile-container, .custom-tile-container")).map(b => (b.innerText || "").replace(/\\n+/g, " ").trim());

        return {
            ok: true,
            url: window.location.href,
            charactersCount: characters.length,
            characters: characters,
            actionTiles: actionTiles
        };
    })()"""
    raw = run_orca_eval(js_code, page_id=page_id)
    try:
        data = json.loads(raw)
        data["nav"] = nav_res
        return data
    except Exception:
        return {"ok": True, "raw": raw}


def open_character_creator(page_id: Optional[str] = None) -> Dict[str, Any]:
    """Navigates to the Character creation screen (/project/<id>/character)."""
    page_id = page_id or find_google_flow_page()
    get_characters_info(page_id=page_id)
    time.sleep(0.5)

    js_code = """(() => {
        const btn = Array.from(document.querySelectorAll("button.custom-tile-container, [class*='custom-tile']")).find(b => (b.innerText || "").includes("New character"));
        if (!btn) return { ok: false, error: "New character button not found" };
        btn.click();
        return { ok: true, clicked: true };
    })()"""
    raw = run_orca_eval(js_code, page_id=page_id)
    time.sleep(1.0)

    # Check state on character page
    check_js = """(() => {
        const archetypes = Array.from(document.querySelectorAll("button")).map(b => (b.innerText || "").replace(/\\n+/g, " ").trim()).filter(t => t.startsWith("The "));
        const promptInput = document.querySelector("[contenteditable='true'], textarea");
        return {
            ok: true,
            url: window.location.href,
            archetypes: archetypes,
            hasPromptInput: !!promptInput,
            promptPlaceholder: promptInput ? (promptInput.getAttribute("placeholder") || "Describe your character…") : null
        };
    })()"""
    state_raw = run_orca_eval(check_js, page_id=page_id)
    try:
        return json.loads(state_raw)
    except Exception:
        return {"ok": True, "raw": state_raw}


def navigate_back_from_creator(page_id: Optional[str] = None) -> Dict[str, Any]:
    """Clicks the back button (arrow_back) to return from character creator to project view."""
    page_id = page_id or find_google_flow_page()
    js_code = """(() => {
        const backBtn = Array.from(document.querySelectorAll("button")).find(b => (b.innerText || "").includes("arrow_back"));
        if (!backBtn) return { ok: false, error: "Back button not found" };
        backBtn.click();
        return { ok: true, clicked: true };
    })()"""
    raw = run_orca_eval(js_code, page_id=page_id)
    time.sleep(1.2)
    url_check = run_orca_eval("window.location.href", page_id=page_id)
    return {"ok": True, "url": url_check.strip()}


def list_sidebar_tabs(page_id: Optional[str] = None) -> Dict[str, Any]:
    """Lists all available sidebar tabs and reports the currently activated section."""
    page_id = page_id or find_google_flow_page()
    js_code = """(() => {
        const drawer = document.querySelector(".mat-drawer-inner-container");
        if (!drawer) return { ok: false, error: "Sidebar drawer not found" };
        const items = Array.from(drawer.querySelectorAll("button, a, [role='button'], mat-list-item"));
        const tabs = [];
        let activeTab = null;
        for (const el of items) {
            const rawText = (el.innerText || "").replace(/\\n+/g, " ").trim();
            if (!rawText) continue;
            const isActive = el.classList.contains("mdc-list-item--activated") || el.getAttribute("aria-current") === "true";
            const tabInfo = {
                title: rawText,
                tag: el.tagName,
                active: isActive
            };
            tabs.push(tabInfo);
            if (isActive) activeTab = rawText;
        }
        return {
            ok: true,
            activeTab: activeTab,
            totalTabs: tabs.length,
            tabs: tabs
        };
    })()"""
    raw = run_orca_eval(js_code, page_id=page_id)
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": True, "raw": raw}


def toggle_sidebar_drawer(page_id: Optional[str] = None) -> Dict[str, Any]:
    """Toggles (collapses/expands) the left sidebar drawer."""
    page_id = page_id or find_google_flow_page()
    js_code = """(() => {
        const collapseBtn = Array.from(document.querySelectorAll(".mat-drawer-inner-container button, .mat-drawer-inner-container a, button")).find(b => {
            const t = (b.innerText || "").toLowerCase();
            const l = (b.getAttribute("aria-label") || "").toLowerCase();
            return t.includes("collapse") || l.includes("collapse") || t.includes("expand") || l.includes("expand") || t.includes("left_panel_close") || t.includes("left_panel_open");
        });
        if (!collapseBtn) return { ok: false, error: "Collapse/expand button not found" };
        collapseBtn.click();
        return { ok: true, clicked: (collapseBtn.innerText || collapseBtn.getAttribute("aria-label") || "toggled").replace(/\\n+/g, " ").trim() };
    })()"""
    raw = run_orca_eval(js_code, page_id=page_id)
    time.sleep(0.5)
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": True, "raw": raw}


def get_gallery_items(page_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves all media, character, and scene tiles currently displayed in the gallery view."""
    page_id = page_id or find_google_flow_page()
    js_code = """(() => {
        const tileContainers = Array.from(document.querySelectorAll("flow-grid-tile-container"));
        const items = tileContainers.map((tc, idx) => {
            const label = tc.getAttribute("aria-label") || ("Item " + (idx + 1));
            const isVideo = !!tc.querySelector("flow-video-tile, [class*='video'], mat-icon[data-mat-icon-name='play_circle']") || tc.innerText.includes("play_circle");
            const isCharacter = !!tc.querySelector(".character-tile-container, [class*='character']") || tc.innerText.includes("accessibility_new");
            const favBtn = tc.querySelector("button[aria-label='Favorite']");
            const reuseBtn = tc.querySelector("button[aria-label='Reuse prompt']");
            const moreBtn = tc.querySelector("button[aria-label='More options']");
            const img = tc.querySelector("img");
            const video = tc.querySelector("video");

            return {
                index: idx + 1,
                label: label,
                type: isCharacter ? "character" : (isVideo ? "video" : "image"),
                hasFavorite: !!favBtn,
                hasReusePrompt: !!reuseBtn,
                hasMoreOptions: !!moreBtn,
                thumbnail: img ? img.src : (video ? video.src : null)
            };
        });
        return {
            ok: true,
            count: items.length,
            items: items
        };
    })()"""
    raw = run_orca_eval(js_code, page_id=page_id)
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": True, "raw": raw}


def trigger_tile_action(target: Any, action: str, page_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes an action on a gallery tile.
    target: 1-based index (int) or case-insensitive string matching the card label.
    action: 'favorite', 'reuse prompt', 'animate', 'add to prompt', 'download', 'copy', 'rename', 'set project cover', 'trash', or 'preview'.
    """
    page_id = page_id or find_google_flow_page()
    act = action.lower().strip()
    js_code = f"""(() => {{
        const targetVal = {json.dumps(target)};
        const actionName = {json.dumps(act)};
        const tiles = Array.from(document.querySelectorAll("flow-grid-tile-container"));
        if (!tiles.length) return {{ ok: false, error: "No gallery tiles found" }};

        let targetTile = null;
        if (typeof targetVal === "number") {{
            const idx = targetVal - 1;
            if (idx >= 0 && idx < tiles.length) targetTile = tiles[idx];
        }} else {{
            const query = String(targetVal).toLowerCase();
            targetTile = tiles.find(t => (t.getAttribute("aria-label") || "").toLowerCase().includes(query));
        }}

        if (!targetTile) return {{ ok: false, error: "Tile not found matching target: " + targetVal }};
        const label = targetTile.getAttribute("aria-label") || "unnamed";

        if (actionName === "favorite") {{
            const favBtn = targetTile.querySelector("button[aria-label='Favorite']");
            if (!favBtn) return {{ ok: false, error: "Favorite button not available on this tile" }};
            favBtn.click();
            return {{ ok: true, action: "favorite", tile: label }};
        }}

        if (actionName === "reuse prompt" || actionName === "reuse") {{
            const reuseBtn = targetTile.querySelector("button[aria-label='Reuse prompt']");
            if (!reuseBtn) return {{ ok: false, error: "Reuse prompt button not available on this tile" }};
            reuseBtn.click();
            return {{ ok: true, action: "reuse_prompt", tile: label }};
        }}

        if (actionName === "preview" || actionName === "click" || actionName === "open") {{
            const mainBtn = targetTile.querySelector("button[aria-label]:not([aria-label='Favorite']):not([aria-label='Reuse prompt']):not([aria-label='More options'])");
            if (mainBtn) mainBtn.click();
            else targetTile.click();
            return {{ ok: true, action: "preview", tile: label }};
        }}

        // Otherwise open More options menu and click menu item
        const moreBtn = targetTile.querySelector("button[aria-label='More options']");
        if (!moreBtn) return {{ ok: false, error: "More options button not available on this tile" }};
        moreBtn.click();

        // Let menu open synchronously in DOM
        const menu = document.querySelector(".mat-mdc-menu-panel, [role='menu']");
        if (!menu) return {{ ok: false, error: "Menu panel failed to open" }};

        const menuItems = Array.from(menu.querySelectorAll("button, [role='menuitem']"));
        const match = menuItems.find(m => (m.innerText || "").toLowerCase().includes(actionName));
        if (!match) {{
            const available = menuItems.map(m => (m.innerText || "").replace(/\\n+/g, " ").trim());
            // close menu
            const backdrop = document.querySelector(".cdk-overlay-backdrop");
            if (backdrop) backdrop.click();
            return {{ ok: false, error: "Action not found in tile menu: " + actionName, available }};
        }}

        match.click();
        return {{ ok: true, action: actionName, tile: label, clickedMenuItem: (match.innerText || "").replace(/\\n+/g, " ").trim() }};
    }})()"""
    raw = run_orca_eval(js_code, page_id=page_id)
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": True, "raw": raw}


def open_add_media_menu(page_id: Optional[str] = None) -> Dict[str, Any]:
    """Opens the top-bar 'Add media' (+) menu and returns available creation actions."""
    page_id = page_id or find_google_flow_page()
    js_code = """(() => {
        const addBtn = document.querySelector("button[aria-label='Add media menu']");
        if (!addBtn) return { ok: false, error: "Add media menu button not found" };
        addBtn.click();

        const menu = document.querySelector(".mat-mdc-menu-panel, [role='menu']");
        if (!menu) return { ok: false, error: "Menu did not open" };
        const items = Array.from(menu.querySelectorAll("button, [role='menuitem']")).map(m => (m.innerText || "").replace(/\\n+/g, " ").trim());
        return {
            ok: true,
            menuOpen: true,
            options: items
        };
    })()"""
    raw = run_orca_eval(js_code, page_id=page_id)
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": True, "raw": raw}


def trigger_add_menu_action(action_name: str, page_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Triggers an action from the top-bar 'Add media' menu.
    Options: 'upload', 'new collection', 'create character', 'new scene'.
    """
    page_id = page_id or find_google_flow_page()
    js_code = f"""(() => {{
        const act = {json.dumps(action_name.lower().strip())};
        const addBtn = document.querySelector("button[aria-label='Add media menu']");
        if (!addBtn) return {{ ok: false, error: "Add media menu button not found" }};
        addBtn.click();

        const menu = document.querySelector(".mat-mdc-menu-panel, [role='menu']");
        if (!menu) return {{ ok: false, error: "Menu did not open" }};
        const items = Array.from(menu.querySelectorAll("button, [role='menuitem']"));
        const match = items.find(m => (m.innerText || "").toLowerCase().includes(act));
        if (!match) {{
            const available = items.map(m => (m.innerText || "").replace(/\\n+/g, " ").trim());
            const backdrop = document.querySelector(".cdk-overlay-backdrop");
            if (backdrop) backdrop.click();
            return {{ ok: false, error: "Option not found in Add menu: " + act, available }};
        }}

        match.click();
        return {{ ok: true, clicked: (match.innerText || "").replace(/\\n+/g, " ").trim(), url: window.location.href }};
    }})()"""
    raw = run_orca_eval(js_code, page_id=page_id)
    time.sleep(0.8)
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": True, "raw": raw}


def toggle_agent_panel(state: str = "toggle", page_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Toggles, opens, or closes the right-side Agent chat & prompt panel.
    state: 'toggle', 'open', or 'close'.
    """
    page_id = page_id or find_google_flow_page()
    js_code = f"""(() => {{
        const desired = {json.dumps(state.lower().strip())};
        const closeBtn = document.querySelector(".agent-panel-animation-container button.close, .agent-panel-wrapper button");
        const expandBtn = document.querySelector("button[aria-label='Expand'], button.expand-button");

        const isOpen = !expandBtn && !!closeBtn;

        if (desired === "close" || (desired === "toggle" && isOpen)) {{
            const btn = Array.from(document.querySelectorAll(".agent-panel-animation-container button")).find(b => (b.innerText || "").trim() === "close");
            if (btn) {{
                btn.click();
                return {{ ok: true, state: "closed" }};
            }}
            return {{ ok: false, error: "Close button not found in panel" }};
        }}

        if (desired === "open" || (desired === "toggle" && !isOpen)) {{
            if (expandBtn) {{
                expandBtn.click();
                return {{ ok: true, state: "open" }};
            }}
            return {{ ok: true, state: "already_open" }};
        }}

        return {{ ok: true, state: isOpen ? "open" : "closed" }};
    }})()"""
    raw = run_orca_eval(js_code, page_id=page_id)
    time.sleep(0.5)
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": True, "raw": raw}


def select_character_archetype(archetype_name: str, page_id: Optional[str] = None) -> Dict[str, Any]:
    """Clicks a character archetype template on the character creation screen."""
    page_id = page_id or find_google_flow_page()
    js_code = f"""(() => {{
        const target = {json.dumps(archetype_name.lower().strip())};
        const buttons = Array.from(document.querySelectorAll("button")).filter(b => (b.innerText || "").trim().startsWith("The "));
        const match = buttons.find(b => (b.innerText || "").toLowerCase().includes(target));
        if (!match) {{
            const available = buttons.map(b => (b.innerText || "").replace(/\\n+/g, " ").trim());
            return {{ ok: false, error: "Archetype not found: " + target, available }};
        }}
        match.click();
        return {{ ok: true, selectedArchetype: (match.innerText || "").replace(/\\n+/g, " ").trim() }};
    }})()"""
    raw = run_orca_eval(js_code, page_id=page_id)
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": True, "raw": raw}


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
    parser.add_argument("--characters", action="store_true", help="Navigate to Characters tab and list characters")
    parser.add_argument("--new-character", action="store_true", help="Open character creation screen")
    parser.add_argument("--back", action="store_true", help="Navigate back from character creation or modal")
    parser.add_argument("--nav", type=str, help="Navigate to sidebar tab (e.g. 'Characters', 'All media', 'Images', 'Videos', 'Scenes', 'Uploads')")
    parser.add_argument("--sidebar-tabs", action="store_true", help="List all sidebar navigation tabs and active status")
    parser.add_argument("--toggle-sidebar", action="store_true", help="Collapse or expand the left sidebar drawer")
    parser.add_argument("--gallery", action="store_true", help="List all media & character tiles in the current view with available actions")
    parser.add_argument("--tile", type=str, help="Target tile index (1-based) or label substring for tile action")
    parser.add_argument("--tile-action", type=str, help="Action to perform on --tile: 'favorite', 'reuse prompt', 'animate', 'add to prompt', 'download', 'copy', 'rename', 'trash', 'preview'")
    parser.add_argument("--add-menu", action="store_true", help="Inspect options in top Add media (+) menu")
    parser.add_argument("--add-action", type=str, help="Trigger action from Add media menu: 'upload', 'new collection', 'create character', 'new scene'")
    parser.add_argument("--toggle-panel", type=str, nargs="?", const="toggle", help="Toggle, open, or close the agent prompt panel ('open', 'close', 'toggle')")
    parser.add_argument("--archetype", type=str, help="Select character archetype template on character creation screen")
    parser.add_argument("--page", type=str, help="Specific browser page ID")
    parser.add_argument("-j", "--json", action="store_true", help="Emit JSON output")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress decorative banners")

    args = parser.parse_args()

    try:
        page_id = args.page or find_google_flow_page()

        if args.sidebar_tabs:
            res = list_sidebar_tabs(page_id=page_id)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print("=" * 75)
                print(f" 📂 SIDEBAR NAVIGATION TABS ({res.get('totalTabs', 0)} items)")
                print(f" Current Active: {res.get('activeTab') or 'None'}")
                print("=" * 75)
                for i, t in enumerate(res.get("tabs", []), 1):
                    act_icon = "⭐ [ACTIVE]" if t.get("active") else "  "
                    print(f"[{i:2d}] {act_icon} {t.get('title')}")
                print("=" * 75)
            return

        if args.toggle_sidebar:
            res = toggle_sidebar_drawer(page_id=page_id)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                if res.get("ok"):
                    print(f"✅ Toggled sidebar drawer: {res.get('clicked')}")
                else:
                    print(f"❌ Failed to toggle sidebar: {res.get('error')}")
            return

        if args.gallery:
            res = get_gallery_items(page_id=page_id)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print("=" * 75)
                print(f" 🖼️ GALLERY VIEW ITEMS ({res.get('count', 0)} items)")
                print("=" * 75)
                for itm in res.get("items", []):
                    type_icon = "🎬" if itm["type"] == "video" else ("🎭" if itm["type"] == "character" else "🖼️")
                    print(f"[{itm['index']:2d}] {type_icon} {itm['label']} ({itm['type'].upper()})")
                    actions = []
                    if itm["hasFavorite"]: actions.append("Favorite")
                    if itm["hasReusePrompt"]: actions.append("Reuse prompt")
                    if itm["hasMoreOptions"]: actions.append("More options")
                    print(f"     Actions: {', '.join(actions)}")
                print("=" * 75)
            return

        if args.tile_action:
            if not args.tile:
                raise GoogleFlowError("--tile <index|name> is required when using --tile-action")
            try:
                target_val = int(args.tile)
            except ValueError:
                target_val = args.tile
            res = trigger_tile_action(target_val, args.tile_action, page_id=page_id)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                if res.get("ok"):
                    print(f"✅ Tile Action Success: [{res.get('tile')}] -> {res.get('action')}")
                else:
                    print(f"❌ Tile Action Error: {res.get('error')}")
            return

        if args.add_menu:
            res = open_add_media_menu(page_id=page_id)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print("=" * 75)
                print(" ➕ ADD MEDIA MENU OPTIONS")
                print("=" * 75)
                for opt in res.get("options", []):
                    print(f"  • {opt}")
                print("=" * 75)
            return

        if args.add_action:
            res = trigger_add_menu_action(args.add_action, page_id=page_id)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                if res.get("ok"):
                    print(f"✅ Clicked Add Menu Action: {res.get('clicked')}")
                else:
                    print(f"❌ Failed Add Menu Action: {res.get('error')}")
            return

        if args.toggle_panel:
            res = toggle_agent_panel(state=args.toggle_panel, page_id=page_id)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                if res.get("ok"):
                    print(f"✅ Agent Panel State: {res.get('state')}")
                else:
                    print(f"❌ Failed to toggle agent panel: {res.get('error')}")
            return

        if args.archetype:
            res = select_character_archetype(args.archetype, page_id=page_id)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                if res.get("ok"):
                    print(f"✅ Selected Archetype: {res.get('selectedArchetype')}")
                else:
                    print(f"❌ Archetype Error: {res.get('error')}")
            return

        if args.nav:
            res = navigate_sidebar_tab(args.nav, page_id=page_id)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                if res.get("ok"):
                    print(f"✅ Navigated to sidebar tab: {res.get('activeText')}")
                else:
                    print(f"❌ Failed to navigate: {res.get('error')}")
            return

        if args.characters:
            chars = get_characters_info(page_id=page_id)
            if args.json:
                print(json.dumps(chars, indent=2))
            else:
                print("=" * 75)
                print(f" 🎭 CHARACTERS IN PROJECT ({chars.get('charactersCount', 0)} characters)")
                print("=" * 75)
                for i, c in enumerate(chars.get("characters", []), 1):
                    print(f"[{i}] 👤 {c.get('name')}")
                    if c.get("thumbnail"):
                        print(f"    Thumbnail: {c.get('thumbnail')[:80]}...")
                print("-" * 75)
                print(f"Available Actions: {', '.join(chars.get('actionTiles', []))}")
            return

        if args.new_character:
            res = open_character_creator(page_id=page_id)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                if res.get("ok"):
                    print("✅ Navigated to Character Creation screen (/character)")
                    print(f"URL: {res.get('url')}")
                    print(f"Prompt Input: {'Ready' if res.get('hasPromptInput') else 'Not found'}")
                    print(f"Sample Archetypes: {', '.join(res.get('archetypes', []))}")
                else:
                    print(f"❌ Failed to open character creator: {res.get('error')}")
            return

        if args.back:
            res = navigate_back_from_creator(page_id=page_id)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                if res.get("ok"):
                    print(f"✅ Navigated back to: {res.get('url')}")
                else:
                    print(f"❌ Failed to navigate back: {res.get('error')}")
            return

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
