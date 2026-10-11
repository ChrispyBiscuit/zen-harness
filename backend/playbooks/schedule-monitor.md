---
name: schedule-monitor
title: Schedule, remind or monitor something
triggers: every day, remind me, schedule, monitor, watch for changes, alert me when, run at, weekly report
examples:
  - "check a page every morning and tell me if the price drops"
  - "run a weekly summary of my folder's new files"
  - "remind me on Fridays to send the report"
applies_when: the user wants something to happen later or repeatedly
---
**Done looks like:** a schedule created with a clear prompt for the future run, confirmed back with its timing; a first run now if useful.
**Approach:** write the future task as a self-contained instruction (what to check, what counts as a change, how to report); schedule_task; list_schedules to confirm; run_now once to validate when cheap.
**Tools:** schedule_task; list_schedules; delete_schedule; the usual tools inside the scheduled prompt.
**Pitfalls:** vague scheduled prompts ("check the site"); schedules that need a login or a user present; duplicates of an existing schedule.
**Verify:** the schedule appears in the list with the right time; the prompt would make sense to a fresh agent.
**Stop / ask:** timing and notification preference if unstated.
