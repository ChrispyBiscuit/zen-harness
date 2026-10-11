---
name: organise-files
title: Organise, rename or move files
triggers: rename, move, organise, sort into folders, clean up the folder, tidy, batch rename, archive old
examples:
  - "rename these files to lowercase with underscores"
  - "sort the downloads folder into subfolders by type"
  - "move last year's files into an archive folder"
  - "rename all the photos in this folder by the date they were taken"
  - "put each file into a subfolder named after its year"
applies_when: the user wants changes to files on disk
---
**Done looks like:** the files changed as asked, a before/after listing, and nothing else touched.
**Approach:** list_dir first; show the planned mapping (old → new) for anything beyond a handful; apply with move_path / make_dir (or a short python_host script for many files); list_dir again.
**Tools:** list_dir; find_files; move_path; make_dir; trash_path (never permanent delete); python_host for bulk operations.
**Pitfalls:** name collisions overwriting files; touching files outside the stated folder; case-only renames on Windows; hidden/system files.
**Verify:** counts before and after match; spot-check names.
**Stop / ask:** show the mapping and confirm before moving or renaming more than ~20 files, or anything irreversible.
