# Lightroom album tree on disk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each Lightroom album lands at its path in the album tree (`2023/Iceland/Day 1/`), existing flat folders move there without a download, and emptied set folders are cleaned up.

**Architecture:** `place()` computes the tree path per album (pure). `read_owned()` finds records at any depth. The executor's existing rename becomes a move into a nested path, followed by pruning the parents it emptied; the same pruning follows a folder that `save()` removes.

**Tech Stack:** Python 3.13, Home Assistant 2026.9.2 custom integration, pytest; HA smoke in the official image (`tests/lightroom_ha_smoke.py`, README "Development").

**Spec:** `docs/superpowers/specs/2026-11-22-lightroom-album-tree-design.md` (goals 1-10 are referred to as G1-G10).

## Global Constraints

- Towards Adobe: GET only; `lightroom_api.py` is not touched.
- Never delete a folder that is not ours or not empty; hidden files and `@` folders count as content.
- No version bump; CHANGELOG under `## Unreleased`.
- Unit tests: `.venv/bin/python -m pytest -q tests`. Smokes: README "Development" `docker run` lines; `lightroom smoke ok`, `albums smoke ok`.
- Examples and test data are invented (no names from a real library).

## Review Focus

1. A set renamed in Lightroom whose old set folder also holds a hand-made file: albums move, the old set folder stays.
2. Two albums whose safe paths are equal (`A/B` and `A/B` from names differing only in `/` vs `_`): the second gets `-2`, a later sync does not swap them.
3. A flat folder whose name equals a set's name in the new tree (album `Iceland` flat, set `Iceland` in the tree): the move does not nest one album inside another.
4. A folder from a previous flat layout under a subfolder (`prefix`): found and moved within the prefix only.
5. An ambiguous key (a copied album folder) in a nested place: neither copy moves, no set folder above it is pruned.

---

### Task 1: Tree paths in `place()`

**Files:** Modify `custom_components/domosenso_photos/lightroom_filter.py` (`place`); Test `tests/test_lightroom_filter.py`.

**Interfaces:**
- Produces: `album_path(album_id: str, albums: dict[str, Album]) -> str` in `lightroom_filter.py`: the safe segments (`folder_name`) of the album's parents (outermost first) and itself joined with `/`; a parent id missing from `albums`, or a cycle, ends the walk there.
- `place()` keeps its signature; `Placed.folder` becomes `<prefix>/<album_path>` (or `album_path` without prefix), with G9's set clash rule applied: when the path equals the path of any set in `albums`, the album's last segment takes `-2`, `-3`, ... until the path is neither a set path nor the path of a different album placed earlier in the order `(path, album id)`.

- [ ] **Step 1: Failing tests** (fixture `ALBUMS`: `S1 Trips` ⊃ `C1 Alps`, `S2 Sub` ⊃ `C2 Deep`):
  - `test_folder_is_the_album_path`: a photo in `C2` → folder `'Trips/Sub/Deep'`; with `prefix='LR'` → `'LR/Trips/Sub/Deep'`; a photo in top-level `A1` → `'Zebra'`.
  - `test_album_path_segments_are_safe`: albums `{'S': album('S', 'a/b', is_set=True), 'C': album('C', '@x', 'S')}` → `'a_b/x'`.
  - `test_album_named_like_a_set_takes_a_suffix`: albums `{'S': set 'Iceland', 'C': album 'Day' in S, 'D': top-level album 'Iceland'}`; photos in C and D → folders `'Iceland/Day'` and `'Iceland-2'`.
  - `test_missing_parent_ends_the_path`: album `C` with parent id `'gone'` → `'<C name>'`.
  - Existing `test_*prefix*` expectations (`'Lightroom/Zebra'`, `'LR/2025-05'`) still hold.
- [ ] **Step 2:** `.venv/bin/python -m pytest -q tests/test_lightroom_filter.py` — the new tests FAIL.
- [ ] **Step 3:** Implement `album_path` and the clash rule in `place()`.
- [ ] **Step 4:** The filter tests pass; the whole suite: tests that assert flat folders of nested albums (in `test_lightroom_sync.py` they build `Placed` by hand, so expect none) are updated to the tree path only where they go through `place()`.
- [ ] **Step 5: Commit** `Lightroom: an album's folder is its path in the album tree`.

### Task 2: Records at any depth, moves into the tree, pruning

**Files:** Modify `custom_components/domosenso_photos/lightroom_sync.py` (`read_owned`, `_Exec.rename`, `_Exec.save`), `custom_components/domosenso_photos/album_layout.py` (new helper); Test `tests/test_lightroom_sync.py`.

**Interfaces:**
- Consumes: `Placed.folder` as tree path (Task 1).
- Produces: `prune_empty(photo_dir: Path, base: str, path: str) -> None` in `album_layout.py`: removes `path`'s parent folders, nearest first, while each is empty (no entry at all) and is strictly below `photo_dir/base` (`base` = the prefix, `''` for none); stops at the first non-empty one.
- `read_owned(photo_dir, prefix, owner)` keeps its signature and result type; it walks the tree below the base (`os.walk`-style, sorted), skips names starting with `.` or `@`, and does not descend into a folder that holds a record of this owner.

- [ ] **Step 1: Failing tests** (build folders with the file's helpers and `run`/`execute`):
  - `test_read_owned_finds_nested_records`: owned folders at `Trips/Alps` and `Zebra` → both keys found with those paths.
  - `test_read_owned_skips_hidden_and_at_folders`: a record of ours inside `@eaDir/x` and `.hidden/y` → not found.
  - `test_flat_folder_moves_into_the_tree`: run once with `placed(1, key='alb1', folder='Day 1')`, then with `placed(1, key='alb1', folder='2023/Iceland/Day 1')` → the photo is at `2023/Iceland/Day 1/`, the record's `path` is `'2023/Iceland/Day 1'`, `Day 1/` at the top is gone, nothing fetched in the second run (count fetch calls).
  - `test_set_rename_moves_albums_and_prunes`: two albums under `Old/`, then placed under `New/` → both under `New/`, `Old/` removed.
  - `test_prune_keeps_a_folder_with_other_content`: as above plus `Old/notes.txt` → `Old/` stays with `notes.txt`.
  - `test_prune_stops_at_the_prefix`: prefix `'lr'`, album `lr/A/B` removed entirely (all photos gone) → `lr/A` removed, `lr` stays.
  - `test_unrelated_empty_folder_is_left`: an empty `Empty/` beside the tree → still there after a sync with moves and removals.
  - `test_rename_ignores_eadir`: an owned folder holding `@eaDir/` moves to its new path with `@eaDir` inside.
  - `test_move_waits_for_an_album_folder_in_the_way`: owned flat folders `Iceland` (key `k1`, now placed at `2023/Iceland`) and `Day` (key `k2`, now placed at `Iceland/Day`, the set `Iceland`) → after one `execute`, `2023/Iceland/` and `Iceland/Day/` exist, each with its record and photos, and no record folder lies inside another record folder.
  - `test_new_album_never_inside_an_album_folder`: an owned folder at `A` and a new album placed at `A/B` → the new photo is written to a free path that is not inside `A` (e.g. `A-2/B`), or skipped this run; never inside `A`.
  - `test_ambiguous_nested_copy_is_untouched`: two folders of one key at `A/X` and `B/X` (both self-recorded paths differ) → plan has no rename for the key, both trees byte-identical after `execute`.
- [ ] **Step 2:** `.venv/bin/python -m pytest -q tests/test_lightroom_sync.py` — new tests FAIL.
- [ ] **Step 3:** Implement: recursive `read_owned`; no move or new folder goes inside a folder that holds one of our records: in `execute`, renames whose target has such an ancestor are retried once after all other renames (the folder in the way has usually moved out by then) and otherwise left for the next sync; `target()` treats such a path like a taken one (next free suffix on the first segment that is an album folder); in `_Exec.rename`, the subfolder guard counts only subfolders whose names do not start with `.` or `@`, then after the move `prune_empty(photo_dir, prefix, old)`; in `_Exec.save`, after removing an emptied folder, `prune_empty(photo_dir, prefix, path)`. `_Exec` needs the prefix: pass it from `execute(..., prefix: str = '')` and from `LightroomManager._sync_body` (`self.prefix`).
- [ ] **Step 4:** Unit suite passes.
- [ ] **Step 5: Commit** `Lightroom: album folders at any depth; moves into the tree prune emptied set folders`.

### Task 3: Smoke, docs

**Files:** Modify `tests/lightroom_ha_smoke.py`, `README.md` (Lightroom placement section), `docs/superpowers/specs/2026-11-22-lightroom-source-design.md` (Placement: point to the amendment), `CHANGELOG.md`.

- [ ] **Step 1: Smoke expectations:** the fake library's two albums inside a set land at `lr/<set>/<album>`; `files()` finds records with `rglob('.sync.json')` and returns paths relative to the photo folder. Add a section: rename the fake set (`payload.name`) and sync → the albums' folders move, `added == 0`, the old set folder is gone; rename it back.
- [ ] **Step 2:** Run the Lightroom smoke — the new section FAILS before Tasks 1-2 are in place is not checkable (they are); run it and expect `lightroom smoke ok`; `albums smoke ok`.
- [ ] **Step 3: Docs.** README: the layout (tree paths, month folders, `-2` rules, set clash, pruning) and that an update from a flat layout moves folders without downloading. CHANGELOG `## Unreleased`: "Lightroom: albums land at their path in the album tree (`<set>/<album>`); folders from the flat layout move there without a download."
- [ ] **Step 4: Commit** `Lightroom album tree: smoke and docs`.
