# Telemetry Tracker (demo)

Internal Audit AI Day, Agent #1.

Telemetry Tracker reads the activity logs AI agents already produce (OpenTelemetry, Langfuse,
ARC / Agent Flow) and checks every action against the rules, automatically. Auditors only see the
handful of things that need a person.

All data is made up. Nothing connects to a real system.

![Telemetry Tracker](media/poster.png)

## Run it

Open `index.html` in Chrome or Edge and press **F11** for full screen. The 15 second walkthrough
plays on its own. It needs no install and works offline.

- `index.html?speed=0.5` plays it at half speed (about 30 seconds) if you want to talk over it.
- `index.html?autoplay=0` waits until you press **Space**.
- Press **R** to restart.

Afterwards you can click any agent to see what it did, or ask the chat a question.

Recorded videos, for slides or as a backup:

- `media/telemetry-tracker-demo-15s.mp4`
- `media/telemetry-tracker-demo-30s.mp4` (half speed)

## The 15 seconds

1. **Check now.** Every action by every agent is checked. Over 1 million actions, only 3 need an auditor.
2. **Same problem in 3 agents.** Hidden instructions in uploaded files are tricking agents. You would
   not spot this from one agent alone.
3. **One agent.** The Payments agent skipped manager approval and sent $2.4M. It went to the audit
   team automatically, with evidence.
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
