# Agent Capabilities & Semantic UI Automation

This document outlines the Smart Semantic DOM Pruning & UI Search module added to the project (`orca_smart_search.py`), as well as the planned capabilities to make the agent faster, more autonomous, and error-proof.

---

## What Was Added Extra

### 1. Smart DOM Pruner & Semantic Search (`orca_smart_search.py`)
- **No LLM Needed**: Fast (5ms), zero-token, algorithmic semantic search using hybrid BM25 + intent synonym expansion.
- **Page Describer**: Automatically scans live pages in the Orca embedded browser to report page category, titles, sections, and lists all available inputs and action buttons.
- **Direct Orca Integration**: Maps user intent (e.g. `"search bar"`, `"phone number"`, `"submit order"`) to actionable element refs (`[ref=e1]`) and outputs ready-to-run CLI commands.

---

## Here are the 5 most useful capabilities to add to make the agent faster, more autonomous, and error-proof:

### 1. Direct Execution (`--act` / One-Step Action)

• **The Problem**: Right now, the agent has to run two steps: first search to get `e3`, then run `orca fill --element e3 --value "..."`.  
• **The Solution**: A single command that finds and interacts in one shot:
```bash
python3 orca_smart_search.py --fill "phone number" "9876543210"
python3 orca_smart_search.py --click "submit order"
```
• **Why it's useful**: Halves the agent's execution time and eliminates manual ref handling.

---

### 2. Batch Form-Fill (Multi-Field at Once)

• **The Problem**: Filling out a ticket or form with 5 fields requires 5 separate tool calls.  
• **The Solution**: Pass a JSON dictionary, and the tool matches and fills all fields simultaneously:
```bash
python3 orca_smart_search.py --form '{"customer name": "Alice", "email": "alice@test.com", "phone": "12345"}'
```
• **Why it's useful**: Submits entire forms or creates Jira tickets in a single command.

---

### 3. Change Verification & Error Detection

• **The Problem**: If an agent clicks "Submit" but a required field was missing, the page shows an error message. A basic agent might assume the action succeeded and get stuck.  
• **The Solution**: After any click or submit, automatically check:
- Did the URL change?
- Did any error banner/text appear (e.g., "This field is required")?
- Did a modal open or close?  
• **Why it's useful**: The agent immediately knows if an action failed and why.

---

### 4. Wait-For-Element (Async / SPA Support)

• **The Problem**: Modern apps like Jira or React dashboards take 1–3 seconds to render dropdowns or modals after a click. If the agent inspects too fast, the field isn't there yet.  
• **The Solution**: Add a wait condition:
```bash
python3 orca_smart_search.py --wait-for "story points" --timeout 5
```
• **Why it's useful**: Prevents false "element not found" failures on dynamic pages.

---

### 5. Machine-Readable JSON Mode (`--json`)

• **The Problem**: Other agents (or code wrappers) have to parse plain text output.  
• **The Solution**: Return structured JSON:
```json
{
  "status": "success",
  "action": "fill",
  "target": "Telephone:",
  "ref": "e3",
  "value": "9876543210"
}
```
• **Why it's useful**: Lets any AI agent or workflow script consume the results reliably.

---

### Which one would you like to add next?

I can implement #1 (Direct One-Step Action) and #2 (Batch Form-Fill) right away into `orca_smart_search.py`. Would you like me to do that?
