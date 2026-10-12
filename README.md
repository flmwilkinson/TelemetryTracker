# Telemetry Tracker: AI agent assurance (demo)

Internal Audit AI Day, Agent #1.

Telemetry Tracker reads the logs that AI agents already produce (OpenTelemetry, Langfuse, ARC / Agent
Flow) and tests every agent action against the control library. It shows risks that repeat across
agents, replays a single agent's decisions, and lets auditors ask questions about the logs in plain
English. AI-detected exceptions stay unvalidated until an auditor accepts or dismisses them.

All data is synthetic. Nothing connects to a real system.

![Telemetry Tracker](media/poster.png)

## Run it

Open `index.html` in Chrome or Edge and press **F11** for full screen. It needs no install and works
offline. The 15 second walkthrough starts on its own.

| Option | What it does |
| --- | --- |
| `index.html?speed=0.5` | Same walkthrough at half speed (about 30 seconds), easier to talk over |
| `index.html?autoplay=0` | Waits on the dashboard until you press **Space** |
| **R** | Restart the walkthrough |

After the walkthrough you can keep using the page. Click any agent row to replay its decision trail,
validate or dismiss a finding, or ask the chat about approvals, the AI inventory, incidents, PII or cost.

Recorded 1080p videos for slides, or as a backup if the live demo fails:

- `media/telemetry-tracker-demo-15s.mp4`
- `media/telemetry-tracker-demo-30s.mp4` (narration pace)

## What the walkthrough shows

| Time | On screen | Why it matters to Internal Audit |
| --- | --- | --- |
| 0 to 3s | **Run review now** tests every agent against 48 controls | Full population tested, no sampling. Finds an agent that is running but missing from the AI inventory |
| 3 to 5.5s | Pattern A, prompt injection via uploaded documents, appears in 3 agents | Thematic issue that no single log would show, mapped to OWASP LLM01 and MITRE ATLAS |
| 5.5 to 9s | Payments Ops decision trail: the approval guardrail was skipped and $2.4M was released | Clear evidence chain. The auditor validates the exception and an issue is raised with owner, due date and a hashed evidence pack |
| 9 to 14s | "Which agents skipped a human approval step this week?" | Three agents and a likely control design gap, with links to the traces behind the answer |

**Suggested voiceover (about 15 seconds):** "Telemetry Tracker reads the logs our AI agents already
produce. One click tests every agent, every action, with no sampling. It spots risks repeating across
agents. Open one up and you see a $2.4 million payment that skipped approval, ready for the auditor to
validate. Or just ask it a question, with the evidence attached."

## What the demo covers for an audit team

- **Completeness:** agents found in telemetry are matched to the AI inventory, and unregistered agents are
  flagged. Log completeness is shown per source, so the team knows how far the data can be relied on.
- **Population testing:** every span is tested, not a sample.
- **Detection method:** each exception says whether a fixed rule or an AI judge (with a confidence score)
  found it.
- **Human validation:** findings start unvalidated. The auditor validates or dismisses them, and
  dismissals are logged so the rule can be tuned.
- **Issue management:** a validated finding becomes an issue with an owner, due date and workpaper
  reference.
- **Evidence:** the evidence pack is hashed and kept in a write-once store. Access to the tool is logged.
- **Mapping:** findings link to internal controls and to external references such as SR 11-7,
  PRA SS1/23, the EU AI Act (Art. 14 human oversight) and OWASP LLM Top 10.

## How it would work for real

- **Ingest:** an OTLP endpoint, the Langfuse public API and the ARC / Agent Flow log export. Spans follow
  the OpenTelemetry `gen_ai.*` semantic conventions (model, tokens, tool calls, guardrail results).
- **Control tests:** fixed rules for clear cases (for example, a payment released with no approval span)
  and an LLM judge for judgement calls (for example, prompt injection), each with its own test record.
- **Cross-agent analysis:** findings are grouped across agents and compared with public incident
  taxonomies to spot repeating failure modes.
- **Ask Telemetry:** an LLM answers questions over the trace store and cites trace IDs. The auditor
  still owns the conclusion.

## Branding

Colours follow the bank's palette: navy `#002D72`, blue `#056DAE`, light blue `#00BDF2` and red
`#DA291C`. To show the official logo in the top bar, save a white version as `assets/logo.svg`.

## Re-record the videos

```bash
npm install
npm run record      # needs ffmpeg; writes media/*.mp4 and media/poster.png
```
