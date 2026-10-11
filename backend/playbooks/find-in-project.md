---
name: find-in-project
title: Find something in a folder or codebase
triggers: where is, which file, find the setting, search the project, where does it define, locate, grep for
examples:
  - "which file sets the timeout for the project"
  - "where is the function that sends emails defined"
  - "find all places that mention a deprecated option"
applies_when: the user wants a location inside their own files
---
**Done looks like:** the file path(s) with the relevant lines quoted, and the value or definition found.
**Approach:** search_text with a precise pattern (the exact key or name) → find_files for likely names → read_file around each hit; for conceptual queries use search_documents (by meaning).
**Tools:** search_text; find_files; read_file (with offset/limit); search_documents.
**Pitfalls:** stopping at the first hit when several files set the same thing (defaults vs overrides); matching inside build or vendor folders; guessing a path instead of searching.
**Verify:** the quoted line is the effective one (check for overrides); paths are given in full.
**Stop / ask:** if the project has several candidates and no way to tell which is live, list them and ask.
