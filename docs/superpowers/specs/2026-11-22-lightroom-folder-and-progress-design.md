# Lightroom entry: change the folder, see the progress

A small change to the Lightroom source (`2026-11-22-lightroom-source-design.md`), asked for after
the first real sync on a home's Home Assistant.

## Why

- The photo folder and subfolder are chosen once, when the entry is added; moving the photos
  somewhere else (for example into a frame's own photo folder) means deleting the entry and signing
  in to Adobe again.
- A sync of a whole library takes tens of minutes and the sensor only says `syncing`: the owner
  cannot tell how far it is.

## Goals

1. **Folder change in Settings.** The Lightroom entry's Settings step gets **Photo folder** and
   **Subfolder**, validated as when the entry is added (absolute existing directory; a subfolder
   that stays inside it). When either changes, a confirmation step says: every photo is downloaded
   again into the new folder, unless that folder already holds this account's photos; the photos in
   the old folder stay where they are and are not deleted by the integration. Only after the
   confirmation are the values saved (in the entry's data) and the entry reloaded; the next sync
   starts as on any reload.
2. **Progress while syncing.** The Lightroom sensor keeps its state (`syncing`, `ok`, …) and, while
   a sync runs, carries the attributes `phase` (`reading_library`, `downloading`, `removing`),
   `done` and `total` (the photos to write, and how many are written or skipped so far). A new
   sensor, **Lightroom sync progress**, in %, shows `done / total` while downloading, 100 after a
   finished run, unknown before the first one and while the library is read.

## Non-goals and anti-goals

- No moving of the existing files to the new folder, and no deletion in the old one (the owner's
  choice: "Leave them"). The integration still writes and deletes only under its current folder.
- No change to the Synology albums entry.
- The progress must not flood the recorder: updates at most every 5 seconds (and one at each phase
  change and at the end).

## Approach

One sensible way: the folder fields join the existing Settings step with one extra confirmation
step, because the entry's data already holds `photo_dir` and `prefix` and the update listener
already reloads the entry; progress is in-memory state of the manager, pushed with the existing
dispatcher signal. A separate "Change folder" menu entry was considered and dropped: one more menu
item for a field that belongs with the other settings.

## Calls made by the builder (correct them if wrong)

- A folder change is refused while a sync runs (error "Wait until the sync has finished"), so a
  reload never cuts a run in the middle.
- Switching back to a folder that still holds this account's photos downloads only what changed:
  ownership is read from the folder's records, as always.
- Texts in en, de, pl; README and the CHANGELOG's Unreleased section updated; no version bump.
