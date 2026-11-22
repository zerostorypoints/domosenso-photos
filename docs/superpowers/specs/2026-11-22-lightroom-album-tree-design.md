# Lightroom source: the album tree on disk

Design, 2026-11-22 (approved). Amends `2026-11-22-lightroom-source-design.md`, "Placement". The
frame side (a Ramka that reads folder trees: caption levels, an album tree, turns level by level)
is a separate change in the Domosenso catalog.

## Why

A Lightroom library is usually a tree: sets (folders of albums), often one per year, one per trip
inside it, an album per day inside that. One flat folder per album loses that context: `Day 1` of
one trip lands next to `Day 1` of another. The photos on disk should keep the tree.

## Goals

1. An album's folder is its path in Lightroom: the names of its parent sets, outermost first, then
   its own name, each made safe by `folder_name` (a `/` becomes `_`, a leading `@` is dropped, an
   empty name becomes `album`), joined with `/`, under the photo folder and subfolder.
   `2023 › Iceland › Day 1` → `<photo_dir>/<prefix>/2023/Iceland/Day 1/`.
2. Photos in no album stay in a month folder `YYYY-MM` directly under the photo folder and
   subfolder.
3. Only album folders carry the record (`.sync.json`) and photos; set folders are plain folders.
4. Records are found at any depth under the photo folder and subfolder, skipping hidden folders and
   folders starting with `@` (a NAS's `@eaDir`). Ownership, copies and ambiguity work as before, per
   album key.
5. A path that changes in Lightroom (an album renamed or moved, a set renamed or moved) moves the
   album's folder to its new path on the next sync, photos and record included, as a flat rename did;
   a set rename moves each of its albums' folders. Nothing is downloaded again.
6. A set folder that this sync emptied, by moving or removing one of its album folders, is deleted,
   walking up to the photo folder and subfolder, while it is empty. Hidden files and `@` folders
   count as content. Any other empty folder is left alone: a set folder has no record that shows it
   is ours.
7. Folders from before this change (flat) are records like any other: the first sync after the
   change moves each into its tree path. Nothing is downloaded again.
8. Safety holds are unchanged; a held sync moves no folder.
9. A move or a new album that would land on a folder that is not ours takes the next free name,
   `-2`, `-3`, as before; parent set folders are shared. An album and a set with the same safe name
   under the same parent: the set keeps the plain name, the album takes the next free suffix, so an
   album folder never holds another album's folder.
10. The guard that keeps a folder with subfolders where it is ignores hidden and `@` subfolders
    (they move with it).

## Non-goals

No change to the filter, to file names (`<YYYY-MM-DD>_<asset id>.jpg`), to the Synology albums
source, or towards Adobe (GET only). No tree for month folders.

## Anti-goals

A move in Lightroom never re-downloads the library, and never deletes a folder that is not the
integration's own or not empty.
