# Your data and privacy

## What stays on your computer
With a local model, the model runs on your PC: your messages, files and the agent's work never leave it — **except**
when a tool reaches the internet:
- **Web search / reading web pages** — your search terms and the pages' addresses go to search engines and those
  websites (as if you searched yourself).
- **Maps and pictures in answers** — a map loads its tiles from OpenStreetMap (which sees the area shown), and a
  place named without coordinates is looked up on OpenStreetMap's Nominatim (only the place name is sent; answers
  are cached on your PC). With web access off, places are not looked up. Pictures in an answer load from the site
  that hosts them.
- **Cloud models** (OpenAI, Anthropic, …) — if you pick one, your conversation and whatever the agent reads are sent
  to that provider under their terms.
- **Cloud advisor** (optional, off by default) — asks a cloud model for a second opinion when the local model is
  stuck; it asks before sending anything.
- **Model downloads** come from Hugging Face.

Zazen has no telemetry and no account.

## Where files live

| What | Where |
|---|---|
| Settings, chats, memory, logs, attachments, checklists, the agent's trash, browser profile | `%LOCALAPPDATA%\ZenHarness\` |
| Models | `%LOCALAPPDATA%\ZenHarness\models\` |
| The program | `%LOCALAPPDATA%\Programs\Zen Harness\` |
| Files the agent creates for you | the chat's working folder, `Documents\Zen Harness\…`, or where you asked |

Help → About has buttons that open these folders.

## Secrets
- Your API token for other apps is in `settings.json` in the data folder.
- Cloud API keys you enter are stored in the data folder too (plain files readable by your Windows account only).
  You can use environment variables instead: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`.

## Removing your data
Uninstall and tick **Also remove my data**, or delete `%LOCALAPPDATA%\ZenHarness` yourself. Individual chats can be
deleted from the sidebar; memories in Settings → Memory.
