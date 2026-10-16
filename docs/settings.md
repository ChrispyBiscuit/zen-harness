# Settings reference

Open Settings with the gear icon in the title bar. Changes apply from your next message after **Save** (the Models
tab saves profiles immediately).

## General
- **Agent model** — the model the agent uses: one of your model profiles, a model file, or a cloud model (OpenAI /
  Anthropic, after adding a key). A profile carries its own context and memory settings.
- **Context window / KV cache** — only for model files without a profile; profiles set these in the Models tab.
- **Response style** — Precise (best for tools and code), Balanced (writing), Focused; Advanced shows the raw
  sampling values.
- **Vision** — send screenshots and attached images to models that can see.
- **Density, notifications, Clip** — appearance and desktop notifications.

## Models
Your model files and profiles; add, set up, re-tune, test-load and remove them. See [Models](models.md).

## Agent
- **Your instructions** — added to every agent chat (e.g. "be concise", "cite primary sources").
- **Understand each request first** — a short "Understood as" summary before work starts.
- **Answer simple questions directly** — quick answers without loading the full agent.
- **Allow sub-tasks** — the agent may run independent parts in background threads.
- **Ask a cloud advisor when stuck** — asks before sending anything.
- **Max steps per message** — it pauses and asks to continue after this many steps.

## Permissions
- **Allowed folders** — where file tools may read and write (one per line).
- **Without asking, it may** — read files, use the web, run read-only commands.
- **Read-only mode** — investigate and propose only.
- **Screen, mouse and keyboard control** — off by default; it asks first.
- **Always allowed** — rules created when you click "Always allow" on an approval.

## Memory
What the agent remembers about you and lessons from past tasks; edit or delete entries.

## Chat & voice
Chat-mode model and system prompt; voice input/output (voice output is an optional download).

## Extensions
Extra tools from **MCP servers** you add (a command to start, or a URL), with a policy per server: ask each time,
allow read-only, or allow all.

## Connect apps
Let other programs use Zen: REST API and MCP switches, token, port, network access, background service at
sign-in, approval policy for tasks from other apps, and ready-to-copy setup for Claude Desktop, Claude Code, Cursor
and scripts. See [Connect other apps](connect-apps.md).
