---
name: make-diagram
title: Create a diagram or chart
triggers: diagram, flowchart, chart, graph, visualise, architecture drawing, sequence diagram, plot
examples:
  - "draw a flowchart of the onboarding process"
  - "chart monthly revenue from this CSV"
  - "an architecture diagram of a three-tier web app"
applies_when: the user wants something visual
---
**Done looks like:** the diagram or chart visible in the Artifacts pane (or saved as a file), correct and readable at a glance.
**Approach:** flows/structures → artifact_create with mermaid; data charts → artifact_create with a Vega-Lite spec (type chart) or python_sandbox + matplotlib saved to a file; keep labels short; one idea per diagram.
**Tools:** artifact_create (mermaid / chart / svg); python_sandbox for computed charts; query_data to get the numbers.
**Pitfalls:** diagrams with 30 boxes; charts of numbers you estimated; unlabelled axes/units.
**Verify:** the artifact renders (artifact_read) and the numbers match the source.
**Stop / ask:** granularity (how much detail) if the subject is large.
