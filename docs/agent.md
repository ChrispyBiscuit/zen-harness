# Using the agent

## Chat and Agent mode

- **Agent** (default) — the assistant can use tools: search the web, read and write files in your allowed folders,
  run commands and programs, use a built-in browser, make Word/Excel/PowerPoint/PDF files, write and debug code, and
  remember things you tell it.
- **Chat** — a plain conversation with the model, no tools.

## What a task looks like

For anything bigger than a quick question, the agent:
1. **Plans** — writes its approach and a **checklist** (shown in the side panel) and, for software, a short PRD
   (`PRD.md`) with acceptance criteria in the project folder.
2. **Works** — each tool call appears as a card you can expand (commands with their output, file diffs, web pages).
3. **Checks** — runs tests or opens what it built before saying it's done; it won't finish while checklist items are
   open.
4. **Reports** — a final answer with what it did, where the files are, and anything it couldn't do.

You can type while it works (it reads your message at the next step), press **Stop**, or say "continue" later.

## Approvals and permissions

Settings → Permissions controls what needs your OK:
- **Folders** — the agent only reads and writes inside these (default: Documents and Desktop). Each chat also gets
  its own working folder under `Documents\Zen Harness`.
- **Without asking** — reading files, web research, read-only commands, writing new files in allowed folders.
- **Asks first** — deleting or moving files, overwriting files it hasn't read, installing software, commands that
  change your system, sending anything to a cloud model. You get a card: **Allow once**, **Always allow** (adds a
  rule), or **Deny**.
- Some actions are always blocked (formatting disks, disabling security, etc.).

Deleted files go to the app's trash, and every file change has a **Revert** button in the chat.

## Background programs

When the agent starts a server or a long-running program it shows up in **Tasks**. Programs it starts are stopped at
the end of its turn — except ones it tells you to open (e.g. "open http://localhost:8790"), which keep running until
you stop them or close the app.

## Memory

The agent remembers facts you tell it ("remember that my project is in D:\work") and useful lessons from past tasks.
Settings → Memory shows, edits and deletes them.

## Tips for good results
- Say what "done" looks like: *"…and make sure the tests pass"*, *"…as a PDF on my Desktop"*.
- For big jobs, larger models do much better; small local models are best at focused tasks.
- If it goes in circles, press Stop and give it a hint, or switch to a larger/cloud model for that chat.
