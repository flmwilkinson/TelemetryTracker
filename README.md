# Telemetry Tracker

Internal Audit AI Day, Agent #1.

Telemetry Tracker reads the activity logs AI agents already produce (OpenTelemetry, Langfuse,
ARC / Agent Flow) and checks every action against the rules, automatically. Auditors only see the
handful of things that need a person.

This repo has two parts:

- **The demo:** `index.html`, a 15 second scripted walkthrough for presenting. It works offline.
- **The agents:** a Python package that does the work for real: reads telemetry, checks it, finds
  patterns across agents and answers questions. It also serves the same dashboard with live results.

![Telemetry Tracker](media/poster.png)

## Run the real agents

Needs Python 3.10 or newer.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                 # then put your API key (and gateway URL, if any) in .env
python -m telemetry_tracker serve    # open http://127.0.0.1:8000 and press "Check now"
```

The two settings that matter in `.env`:

| Setting | What to put |
| --- | --- |
| `ANTHROPIC_API_KEY` | Your API key. |
| `ANTHROPIC_BASE_URL` | Leave blank for api.anthropic.com, or your internal gateway URL. The gateway must serve the Anthropic Messages API. If it rejects beta parameters, also set `TT_REFUSAL_FALLBACK=false`. |

Without a key everything still runs: the fixed rules check every action, and the AI checks fall
back to simple rules. Only the chat needs the key.

Other commands:

```bash
python -m telemetry_tracker check                                   # run the checks, print the results
python -m telemetry_tracker ask "Which agents skipped approvals?"   # one question to Ask Telemetry
python -m telemetry_tracker sample                                  # regenerate the sample telemetry
python -m unittest discover tests                                   # tests (no key or network needed)
```

## The three agents

All three use one LLM client (`telemetry_tracker/llm.py`), configured from `.env`.

**1. Reviewer** (`agents/reviewer.py`) checks one agent at a time.

- Fixed rules (`rules.py`) run on every action, because they are cheap:
  - payments over $1M and fund releases over $100k need a `human_approval` step first;
  - suppliers must be sanctions-screened before payment;
  - agents may only use their approved tools and AI models;
  - emails may only go to bank addresses;
  - agents must be in the AI inventory.
- A log only records what happened, so a skipped step is found by its absence: the rule expects
  an approval event and there is none.
- One check needs judgement: did a document the agent read contain hidden instructions for an AI,
  and did the agent act on them? This uses the LLM as a judge, with structured output. A cheap
  pre-filter means only suspicious documents reach the judge, and verdicts are cached.
- Rule breaks go to the audit team's queue automatically (`out/audit_queue.jsonl`, which stands in
  for a case-management system).

**2. Pattern finder** (`agents/patterns.py`) looks across all agents.

- Groups findings by rule across agents, compares this week with the usual weekly rate, and looks
  for a setup every affected run shares.
- On the sample data it works out on its own that every unapproved payment ran on Agent Flow
  template `funds-release@2.3`, where approval is `advisory`.
- The LLM writes the plain-English summary and matches it to known incident types
  (`data/incidents.json`: OWASP LLM Top 10, MITRE ATLAS).

**3. Ask Telemetry** (`agents/ask.py`) is a chat agent with read-only tools over the results:

- `list_agents`
- `find_events`
- `get_trace`
- `list_findings`
- `list_patterns`

It decides which tools to call, answers in plain English, and cites trace IDs as evidence. Follow-up
questions keep the conversation.

## Plug in real telemetry

- **OpenTelemetry:** point `TT_OTLP_FILE` at an OTLP/JSON export, for example from the OpenTelemetry
  Collector's file exporter. Spans should follow the GenAI semantic conventions:
  - `gen_ai.operation.name` with the values `invoke_agent`, `chat` and `execute_tool`;
  - `gen_ai.tool.name`, `gen_ai.tool.call.arguments` and `gen_ai.tool.call.result`;
  - `service.name` set to the agent's ID.
- **Langfuse:** set `TT_SOURCE=langfuse`, plus `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY` and
  `LANGFUSE_SECRET_KEY`. Each trace's name, or `metadata.agent`, is the agent ID.
- **The AI inventory** is `data/agents.json`: each agent's owner, approved tools and models, and the
  approved email domains.

Two things the logs must contain for the checks to work:

- tool inputs and outputs, such as the payment amount;
- document text, or an excerpt, for the hidden-instruction check.

Both are opt-in in most instrumentation.

## Present the demo

Open `index.html` in Chrome or Edge and press **F11**. When opened as a file it plays the scripted
15 second walkthrough. When served by `python -m telemetry_tracker serve` it switches to live mode;
add `?demo=1` to force the scripted demo.

- `index.html?speed=0.5` plays it at half speed (about 30 seconds) if you want to talk over it.
- `index.html?autoplay=0` waits until you press **Space**.
- Press **R** to restart.

Recorded videos, for slides or as a backup:

- `media/telemetry-tracker-demo-15s.mp4`
- `media/telemetry-tracker-demo-30s.mp4` (half speed)

### The 15 seconds

1. **Check now.** Every action by every agent is checked. Over 1 million actions, only 3 need an auditor.
2. **Same problem in 3 agents.** Hidden instructions in uploaded files are tricking agents. You would
   not spot this from one agent alone.
3. **One agent.** The Payments agent's own log shows it checked sanctions and then released $2.4M.
   Telemetry Tracker tests that log against the rules and finds no human approval before the payment.
   It goes to the audit team automatically, with the log as evidence.
4. **Ask a question.** "Which agents skipped approvals this week?" Two agents, same cause, one fix.

**Voiceover:** "Our AI agents take over a million actions a day. Telemetry Tracker checks every one of
them, automatically. It spots problems spreading across agents, shows exactly what went wrong, and only
sends auditors the few things that matter. And you can just ask it."

## Branding

Colours follow the bank's palette: navy `#002D72`, blue `#056DAE`, light blue `#00BDF2` and red
`#DA291C`. To show the official logo in the top bar, save a white version as `assets/logo.svg`.

## Re-record the videos

```bash
npm install
npm run record      # needs ffmpeg
```

All data in this repo is synthetic.
