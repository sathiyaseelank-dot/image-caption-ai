#!/usr/bin/env python3
"""
Story-to-Flow Autonomous Director
================================
End-to-end orchestration bridge connecting:
  1. User high-level story brief
  2. ChatGPT / Web AI prompt enhancer & scene breakdown generator
  3. Google Flow execution, auto-approvals, queue monitoring, and asset collection

Usage:
  # 1. Break story into scenes & enhance via ChatGPT
  python3 story_to_flow_director.py --story "A brave warrior boy discovers an ancient temple in the jungle" --scenes 3

  # 2. End-to-end: Enhance story with ChatGPT and directly execute scene 1 in Google Flow
  python3 story_to_flow_director.py --story "Kannagi looks at the Kovalan mansion courtyard" --execute

  # 3. Enhance only and emit structured JSON for AGY / Agent to review
  python3 story_to_flow_director.py --story "..." --dry-run --json
"""

import sys
import os
import json
import time
import argparse
from typing import Dict, Any, List, Optional

# Import coworkers
from chatgpt_coworker import ask_chatgpt
from google_flow_coworker import (
    get_project_status,
    send_prompt,
    handle_approval,
    get_generated_media,
    find_google_flow_page
)


DIRECTOR_SYSTEM_INSTRUCTION = """
You are an expert cinematic visual director and prompt engineer for Google Flow (VideoFX, Imagen 3, Nano Banana 2.1, Omni Flash).
Given a user's story or scene idea, break it down into sequential, highly cinematic scene prompts optimized for Google Flow.

For EACH scene, generate:
1. Scene Title
2. Scene Goal / Action (what happens in 6 seconds)
3. Google Flow Prompt formatted with:
   - SUBJECT: character/focal point, identity preservation rules
   - POSE & ACTION: subtle natural human movement
   - ENVIRONMENT: lighting, atmosphere, time of day
   - CAMERA: angle, focal length, framing (e.g. 9:16 vertical or 16:9 cinematic)
   - STYLE: ultra-photorealistic historical film, 4k texture, cinematic lighting
   - STRICT RULES: no modern objects, no text, no watermark, no scene cuts

Output MUST be valid JSON with the format:
{
  "story_title": "Title",
  "scenes": [
    {
      "scene_number": 1,
      "title": "Scene Name",
      "action_summary": "Summary",
      "flow_prompt": "SUBJECT: ...\\nPOSE: ...\\nENVIRONMENT: ...\\nCAMERA: ...\\nSTYLE: ...\\nSTRICT RULES: ..."
    }
  ]
}
"""


def enhance_story_with_chatgpt(story_text: str, num_scenes: int = 3) -> Dict[str, Any]:
    """
    Sends the user's story to ChatGPT to be enhanced and broken down into
    Google Flow formatted prompts.
    """
    prompt = (
        f"{DIRECTOR_SYSTEM_INSTRUCTION}\n\n"
        f"USER STORY IDEA:\n\"{story_text}\"\n\n"
        f"Please break this into {num_scenes} continuous cinematic scenes for Google Flow. Output JSON ONLY."
    )

    print("🤖 Asking ChatGPT coworker to break down & enhance story prompts...")
    response_text = ask_chatgpt(prompt, timeout=120)

    # Clean potential markdown fences ```json ... ```
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
        return data
    except Exception:
        # Fallback if ChatGPT returned text wrapping the JSON
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(cleaned[start:end+1])
            except Exception:
                pass
        return {
            "story_title": "Custom Story",
            "scenes": [
                {
                    "scene_number": 1,
                    "title": "Scene 1",
                    "action_summary": story_text,
                    "flow_prompt": response_text
                }
            ]
        }


def execute_scene_in_flow(flow_prompt: str, auto_approve: bool = True) -> Dict[str, Any]:
    """
    Sends the enhanced prompt to Google Flow and auto-handles generation.
    """
    print(f"\n🚀 Dispatching prompt to Google Flow...")
    res = send_prompt(flow_prompt, auto_approve=auto_approve)
    return res


def main():
    parser = argparse.ArgumentParser(description="Story-to-Flow Autonomous Director")
    parser.add_argument("-s", "--story", type=str, required=True, help="Raw story or concept from user/AGY")
    parser.add_argument("-n", "--scenes", type=int, default=3, help="Number of scenes to generate (default: 3)")
    parser.add_argument("-e", "--execute", action="store_true", help="Execute scene 1 directly in Google Flow")
    parser.add_argument("--scene-idx", type=int, default=1, help="Specific scene number to execute (default: 1)")
    parser.add_argument("--dry-run", action="store_true", help="Enhance with ChatGPT without sending to Flow")
    parser.add_argument("-j", "--json", action="store_true", help="Output pure JSON")
    args = parser.parse_args()

    # 1. Enhance story with ChatGPT
    story_plan = enhance_story_with_chatgpt(args.story, num_scenes=args.scenes)

    if args.dry_run or not args.execute:
        if args.json:
            print(json.dumps(story_plan, indent=2))
        else:
            print("=" * 75)
            print(f" 🎬 STORY PLAN: {story_plan.get('story_title', 'Cinematic Story')}")
            print("=" * 75)
            for sc in story_plan.get("scenes", []):
                print(f"▶️ Scene {sc.get('scene_number')}: {sc.get('title')}")
                print(f"   Summary: {sc.get('action_summary')}")
                print(f"   Prompt:\n{sc.get('flow_prompt')}")
                print("-" * 75)
            print("💡 Tip: Add --execute to push scene directly into Google Flow.")
        return

    # 2. Execution mode
    scenes = story_plan.get("scenes", [])
    target_scene = next((s for s in scenes if s.get("scene_number") == args.scene_idx), (scenes[0] if scenes else None))

    if not target_scene:
        print("❌ No scenes available to execute.", file=sys.stderr)
        sys.exit(1)

    print(f"\n🎯 Selected Scene {target_scene.get('scene_number')}: {target_scene.get('title')}")
    flow_prompt = target_scene.get("flow_prompt", "")

    result = execute_scene_in_flow(flow_prompt, auto_approve=True)

    if args.json:
        print(json.dumps({
            "story_plan": story_plan,
            "executed_scene": target_scene,
            "flow_result": result
        }, indent=2))
    else:
        print("\n" + "=" * 75)
        print(" 🎉 SCENE DISPATCHED TO GOOGLE FLOW")
        print("=" * 75)
        print(f"Prompt sent: {flow_prompt[:120]}...")
        stat = result.get("status", {})
        print(f"Queue Status: {'Queued on server' if stat.get('isQueued') else 'Processing'}")
        print("=" * 75)


if __name__ == "__main__":
    main()
