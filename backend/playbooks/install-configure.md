---
name: install-configure
title: Install or configure software
triggers: install, set up, configure, enable, settings for, get it working, upgrade
examples:
  - "install the CLI tool and make it available in the terminal"
  - "configure the editor to format on save"
  - "upgrade the package to the latest version"
applies_when: the user wants software installed or its settings changed on this PC
---
**Done looks like:** the software installed/configured, proven by a version or status command, and the exact steps taken.
**Approach:** check what is already there (version command); use the official install method; one change at a time; verify after each; keep a note of what changed for rollback.
**Tools:** powershell (with approval for installs); read_file / edit_file for config files; web_search for the official docs when unsure.
**Pitfalls:** installing into the wrong environment (system vs project venv); changing global settings for a project need; unofficial installers.
**Verify:** the version/status command succeeds; the configured behaviour is observed.
**Stop / ask:** before system-wide changes, admin rights, or anything that replaces an existing version.
