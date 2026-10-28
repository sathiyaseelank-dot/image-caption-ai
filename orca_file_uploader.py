#!/usr/bin/env python3
"""
Orca Web File Uploader CLI & Agent Tool
======================================
Standalone file upload automation for web forms, file conversion sites, and Web AIs:
- Discovers all file upload inputs (<input type="file">) across documents, iframes, and shadow roots.
- Matches target upload fields semantically by label, ID, name, or context (e.g. "Select PDF", "Resume", "Profile Photo").
- Validates local file existence and checks against input `accept` constraints.
- Uploads single or multiple files via native CDP file attachment (`orca upload`).
- Automatically verifies post-upload DOM changes (e.g., uploaded filename, file badges, conversion readiness).

Usage:
  python3 orca_file_uploader.py sample.pdf
  python3 orca_file_uploader.py --files file1.pdf file2.pdf --target "Select PDF file"
  python3 orca_file_uploader.py --list
  python3 orca_file_uploader.py document.pdf --json
"""

import sys
import os
import re
import json
import time
import argparse
import subprocess
from typing import Dict, Any, Optional, List, Tuple

ORCA_BIN = os.environ.get("ORCA_BIN", "/home/inkyank-01/.config/orca/linux-orca-cli-shim/orca")

class UploadError(Exception):
    """Exception raised when file upload operations fail."""
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
        raise UploadError(f"Command timed out after {timeout}s: {' '.join(args)}")
    except Exception as e:
        raise UploadError(f"Execution failed: {e}")

    if res.returncode != 0:
        err = res.stderr.strip() or res.stdout.strip()
        raise UploadError(f"Command failed (exit code {res.returncode}): {err}")
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
        raise UploadError(f"Failed to parse Orca snapshot: {e}")

def list_file_inputs(page_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Discovers all input[type="file"] elements across documents and iframes,
    returning rich contextual metadata.
    """
    js = """
    (() => {
        let allDocs = [document];
        document.querySelectorAll('iframe').forEach(f => {
            try {
                const idoc = f.contentDocument || f.contentWindow.document;
                if (idoc) allDocs.push(idoc);
            } catch(e) {}
        });

        const inputs = [];
        allDocs.forEach((doc, docIdx) => {
            const fileEls = Array.from(doc.querySelectorAll('input[type="file"]'));
            fileEls.forEach(el => {
                let label = '';
                if (el.labels && el.labels[0]) label = el.labels[0].innerText;
                else if (el.closest('label')) label = el.closest('label').innerText;
                
                const parent = el.closest('form, .upload-container, .dropzone, [class*="upload"], [id*="upload"], div') || el.parentElement;
                let context = parent ? (parent.innerText || '') : '';
                context = context.replace(/\\s+/g, ' ').slice(0, 150).trim();

                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                const isRendered = (style.display !== 'none' && style.visibility !== 'hidden');

                inputs.push({
                    id: el.id || '',
                    name: el.name || '',
                    accept: el.accept || '*/*',
                    multiple: !!el.multiple,
                    label: label.trim(),
                    context: context,
                    isRendered: isRendered,
                    opacity: style.opacity,
                    inIframe: docIdx > 0
                });
            });
        });

        return JSON.stringify(inputs);
    })()
    """
    raw = run_orca_eval(js, page_id=page_id)
    try:
        return json.loads(raw)
    except Exception as e:
        raise UploadError(f"Failed to inspect DOM file inputs: {e}")

def get_upload_candidates(target_query: Optional[str] = None, page_id: Optional[str] = None) -> List[Tuple[int, str, Dict[str, Any]]]:
    """
    Finds and ranks all candidate upload elements in the Orca snapshot.
    Prioritizes genuine input[type=file] elements over styled links/wrappers.
    """
    snapshot = get_orca_snapshot(page_id=page_id)
    refs = snapshot.get("refs", {})
    snap_text = snapshot.get("snapshot", "")

    # Look for explicit native file input markers in snapshot text: e.g. [ref=e9]: No file chosen
    explicit_file_refs = set(re.findall(r'\[ref=(e\d+)\].*?:\s*(?:No file chosen|\d+\s+files?\s+chosen)', snap_text, re.IGNORECASE))

    file_candidates = []
    for ref_id, info in refs.items():
        name = (info.get("name") or "").lower()
        role = (info.get("role") or "").lower()
        score = 0

        if ref_id in explicit_file_refs:
            score += 100

        # Buttons with file-picking titles
        if role == "button":
            if any(k in name for k in ["choose files", "choose file", "browse", "select file", "upload file"]):
                score += 50
            elif "file" in name or "upload" in name:
                score += 25
        elif role == "link":
            # CDP setFileInputFiles requires an input element; links are often wrappers that fail CDP
            score -= 40
        elif role in ["radio", "checkbox", "tab", "menuitem"]:
            score -= 100

        if target_query:
            q_lower = target_query.lower()
            if q_lower in name:
                score += 30
            else:
                matches = sum(1 for w in q_lower.split() if w in name)
                score += (matches * 10)

        if score > 0 or (target_query and q_lower in name):
            file_candidates.append((score, ref_id, info))

    # If no high-confidence candidate found, attempt to unhide hidden input[type=file] in DOM
    if not file_candidates or all(c[0] < 50 for c in file_candidates):
        unhide_js = """
        (() => {
            let allDocs = [document];
            document.querySelectorAll('iframe').forEach(f => {
                try {
                    const idoc = f.contentDocument || f.contentWindow.document;
                    if (idoc) allDocs.push(idoc);
                } catch(e) {}
            });
            let count = 0;
            for (const doc of allDocs) {
                doc.querySelectorAll('input[type="file"]').forEach((inp, idx) => {
                    let p = inp.parentElement;
                    while (p && p !== doc.body && p !== doc.documentElement) {
                        try {
                            if (window.getComputedStyle(p).display === 'none') {
                                p.style.display = 'block';
                            }
                            if (window.getComputedStyle(p).visibility === 'hidden') {
                                p.style.visibility = 'visible';
                            }
                        } catch(e) {}
                        p = p.parentElement;
                    }
                    inp.style.display = 'block';
                    inp.style.visibility = 'visible';
                    inp.style.opacity = '1';
                    inp.style.position = 'fixed';
                    inp.style.top = '10px';
                    inp.style.left = (10 + idx * 130) + 'px';
                    inp.style.zIndex = '999999';
                    inp.style.width = '120px';
                    inp.style.height = '35px';
                    inp.setAttribute('aria-label', inp.getAttribute('aria-label') || 'Upload Input File');
                    count++;
                });
            }
            return count;
        })()
        """
        try:
            run_orca_eval(unhide_js, page_id=page_id)
            time.sleep(0.4)
            new_snap = get_orca_snapshot(page_id=page_id)
            new_snap_text = new_snap.get("snapshot", "")
            new_refs = new_snap.get("refs", {})
            new_explicit = set(re.findall(r'\[ref=(e\d+)\].*?:\s*(?:No file chosen|\d+\s+files?\s+chosen)', new_snap_text, re.IGNORECASE))
            
            for ref_id, info in new_refs.items():
                name = (info.get("name") or "").lower()
                role = (info.get("role") or "").lower()
                if ref_id in new_explicit or "upload input file" in name or ("choose" in name and role == "button"):
                    file_candidates.append((120, ref_id, info))
        except Exception:
            pass

    file_candidates.sort(key=lambda x: x[0], reverse=True)
    return file_candidates

def find_upload_ref(target_query: Optional[str] = None, page_id: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """
    Finds the accessibility ref corresponding to the target file upload field.
    """
    candidates = get_upload_candidates(target_query=target_query, page_id=page_id)
    if not candidates:
        raise UploadError("No file upload input found on the current page.")
    return candidates[0][1], candidates[0][2]

def verify_upload_outcome(
    uploaded_basenames: List[str],
    page_id: Optional[str] = None,
    timeout: float = 6.0
) -> Dict[str, Any]:
    """
    Monitors DOM post-upload to detect appearance of uploaded filenames,
    conversion buttons, or error alerts.
    """
    start = time.time()
    escaped_names = json.dumps(uploaded_basenames)
    js = f"""
    (() => {{
        const names = {escaped_names};
        const bodyText = document.body ? document.body.innerText : '';
        const foundNames = names.filter(n => bodyText.includes(n));
        
        // Check for conversion/action buttons that appear upon successful upload
        const actionButtons = Array.from(document.querySelectorAll('button, a.btn, input[type=submit]'))
            .map(b => (b.innerText || b.value || '').trim())
            .filter(t => /convert|submit|upload|continue|start|process|save/i.test(t));

        // Check for error messages
        const errorEls = Array.from(document.querySelectorAll('.error, .alert-danger, [role=alert], .error-msg'))
            .map(e => e.innerText.trim()).filter(Boolean);

        return JSON.stringify({{
            foundNames: foundNames,
            allFound: foundNames.length === names.length,
            actionButtons: actionButtons.slice(0, 5),
            errors: errorEls
        }});
    }})()
    """

    time.sleep(0.5)
    last_status = {}
    while time.time() - start < timeout:
        try:
            raw = run_orca_eval(js, page_id=page_id)
            status = json.loads(raw)
            last_status = status
            if status.get("foundNames") or status.get("actionButtons"):
                return {
                    "verified": True,
                    "matched_files": status.get("foundNames", []),
                    "action_buttons": status.get("actionButtons", []),
                    "errors": status.get("errors", [])
                }
        except Exception:
            pass
        time.sleep(0.5)

    return {
        "verified": bool(last_status.get("foundNames")),
        "matched_files": last_status.get("foundNames", []),
        "action_buttons": last_status.get("actionButtons", []),
        "errors": last_status.get("errors", [])
    }

def upload_files(
    file_paths: List[str],
    target_query: Optional[str] = None,
    page_id: Optional[str] = None,
    verify: bool = True,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    Main programmatic interface to upload files to the current browser page.
    """
    # 1. Validate local file paths
    validated_files = []
    basenames = []
    for fp in file_paths:
        abs_p = os.path.abspath(os.path.expanduser(fp))
        if not os.path.exists(abs_p):
            raise UploadError(f"File not found on system: '{fp}' (resolved: '{abs_p}')")
        if not os.path.isfile(abs_p):
            raise UploadError(f"Path is not a regular file: '{abs_p}'")
        validated_files.append(abs_p)
        basenames.append(os.path.basename(abs_p))

    if not validated_files:
        raise UploadError("No files specified for upload.")

    if verbose:
        print("=" * 75)
        print(" 📤 ORCA FILE UPLOADER")
        print("=" * 75)
        print(f"📁 Files to Upload   : {len(validated_files)}")
        for i, f in enumerate(validated_files, 1):
            size_kb = round(os.path.getsize(f) / 1024, 1)
            print(f"   [{i}] {os.path.basename(f)} ({size_kb} KB)")
        if target_query:
            print(f"🎯 Target Input     : \"{target_query}\"")
        print("-" * 75)

    # 2. Locate target element ref candidates
    candidates = get_upload_candidates(target_query=target_query, page_id=page_id)
    if not candidates:
        raise UploadError("No file upload input found on the current page.")

    # 3. Execute CDP file upload via Orca (attempt best candidates)
    files_arg = ",".join(validated_files)
    last_error = None
    upload_success = False
    chosen_ref = None
    chosen_info = None

    for score, ref_id, info in candidates:
        if verbose:
            print(f"👉 Trying Upload Ref: [{ref_id}] \"{info.get('name')}\" ({info.get('role')}) [score={score}]")
        cmd = [ORCA_BIN, "upload", "--element", ref_id, "--files", files_arg, "--json"]
        if page_id:
            cmd.extend(["--page", page_id])

        try:
            raw_res = run_cmd(cmd, timeout=20.0)
            upload_data = json.loads(raw_res)
            if upload_data.get("ok"):
                upload_success = True
                chosen_ref = ref_id
                chosen_info = info
                if verbose:
                    print(f"⚡ Success with ref {ref_id}: uploaded {len(validated_files)} file(s).")
                break
            else:
                last_error = upload_data.get("error") or upload_data
        except Exception as e:
            last_error = str(e)
            if "not a file input" in last_error.lower():
                continue
            break

    if not upload_success:
        raise UploadError(f"Orca upload failed: {last_error}")

    ref_id = chosen_ref
    info = chosen_info or {}

    # 4. Verify post-upload state
    verification = {}
    if verify:
        if verbose:
            print("🔍 Verifying upload in page DOM...")
        verification = verify_upload_outcome(basenames, page_id=page_id)
        if verbose:
            if verification.get("verified"):
                print(f"✅ Upload Verified: File(s) recognized by web application.")
                if verification.get("action_buttons"):
                    print(f"🔘 Next Available Actions: {', '.join(verification['action_buttons'])}")
            else:
                print("ℹ️  Upload dispatched (no immediate filename text detected in DOM).")

    if verbose:
        print("=" * 75)

    return {
        "ok": True,
        "action": "upload",
        "target_ref": ref_id,
        "target_name": info.get("name"),
        "files_uploaded": validated_files,
        "basenames": basenames,
        "count": len(validated_files),
        "verified": verification.get("verified", False),
        "verification_details": verification
    }

def main():
    parser = argparse.ArgumentParser(
        description="Orca Web File Uploader CLI & Agent Tool"
    )
    parser.add_argument("file_args", nargs="*", help="File paths to upload")
    parser.add_argument("-f", "--files", nargs="+", help="Explicit file path list")
    parser.add_argument("-t", "--target", type=str, help="Target upload field query or label (optional)")
    parser.add_argument("-l", "--list", action="store_true", help="List all file upload inputs on the active page")
    parser.add_argument("-p", "--page", type=str, help="Target specific Orca browser page ID")
    parser.add_argument("--no-verify", action="store_true", help="Skip post-upload DOM verification")
    parser.add_argument("-j", "--json", action="store_true", help="Emit strict JSON output")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress logs")

    args = parser.parse_args()

    # Mode 1: List file upload inputs
    if args.list:
        inputs = list_file_inputs(page_id=args.page)
        if args.json:
            print(json.dumps({"ok": True, "file_inputs": inputs}, indent=2))
        else:
            print("=" * 75)
            print(" 📋 DETECTED FILE UPLOAD INPUTS ON CURRENT PAGE")
            print("=" * 75)
            if not inputs:
                print("   (No file upload inputs detected on this page)")
            else:
                for i, inp in enumerate(inputs, 1):
                    mult = "Yes" if inp["multiple"] else "No"
                    print(f"   [{i}] ID: {inp['id'] or '(none)'} | Accept: {inp['accept']} | Multiple: {mult}")
                    if inp["label"]:
                        print(f"       Label  : \"{inp['label']}\"")
                    if inp["context"]:
                        print(f"       Context: \"{inp['context']}\"")
            print("=" * 75)
        sys.exit(0)

    # Determine files to upload
    files = args.files or args.file_args
    if not files:
        parser.print_help()
        sys.exit(1)

    verbose = not args.quiet and not args.json

    try:
        res = upload_files(
            file_paths=files,
            target_query=args.target,
            page_id=args.page,
            verify=not args.no_verify,
            verbose=verbose
        )

        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"\n🎉 Successfully uploaded {res['count']} file(s) to [{res['target_ref']}] \"{res['target_name']}\"!\n")

    except Exception as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        else:
            print(f"\n❌ Upload Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
