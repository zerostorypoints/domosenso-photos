# Lightroom folder change and sync progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Lightroom entry shows how far a sync is, and its folder and subfolder can be changed in Settings after a confirmation.

**Architecture:** `execute()` reports progress through an optional callback; the manager keeps it in memory and pushes it (throttled) with the existing dispatcher signal to the Lightroom sensor's attributes and a new percentage sensor. The Settings step of the options flow gains the two folder fields and a confirmation step that updates the entry's data and options in one call; the update listener reloads when the folder differs.

**Tech Stack:** Python 3.13, Home Assistant 2026.9.2 custom integration, pytest; HA smoke in the official HA image (`tests/lightroom_ha_smoke.py`, README "Development").

**Spec:** `docs/superpowers/specs/2026-11-22-lightroom-folder-and-progress-design.md` (parent: `2026-11-22-lightroom-source-design.md`).

## Global Constraints

- Towards Adobe: GET only. No change to `lightroom_api.py`; no agent gets Adobe credentials or real-account access.
- The integration writes and deletes only under its current `photo_dir`/`prefix`; nothing in the old folder is touched after a folder change.
- Progress updates reach the state machine at most every 5 seconds, plus one at each phase change and one when `done == total`.
- Phases, exactly: `reading_library`, `downloading`, `removing`. Attributes, exactly: `phase`, `done`, `total` (all `None` when no sync runs).
- Texts in `strings.json`, `translations/en.json` (equal to strings), `translations/de.json`, `translations/pl.json`.
- No version bump (manifest stays `0.2.0`); CHANGELOG entries go under `## Unreleased`.
- Unit tests: `.venv/bin/python -m pytest -q tests`. HA smoke: the `docker run` line in README "Development", for `tests/lightroom_ha_smoke.py`; last line `lightroom smoke ok`.

## Review Focus

1. A folder change submitted while a sync runs: refused with `sync_running`, entry unchanged (checked again on the confirm step).
2. Settings saved with the folder unchanged: no confirmation step, behaves as today (one reload only when options changed).
3. A folder change also changing interval etc.: exactly one reload, both saved.
4. A plan with nothing to write or remove: progress ends at 100, no division by zero.
5. A sync that fails mid-download: `phase/done/total` go back to `None` and the progress sensor does not claim 100 for that run.

---

### Task 1: Sync progress

**Files:**
- Modify: `custom_components/domosenso_photos/lightroom_sync.py` (`execute`)
- Modify: `custom_components/domosenso_photos/lightroom.py` (`LightroomManager`)
- Modify: `custom_components/domosenso_photos/sensor.py`
- Test: `tests/test_lightroom_sync.py`, `tests/lightroom_ha_smoke.py`
- Docs: `README.md` (the Lightroom sensor section), `CHANGELOG.md`

**Interfaces:**
- Produces: `execute(..., now: int, progress: Callable[[str, int, int], None] | None = None) -> RunResult`; called as `progress(phase, done, total)` with `total = len(writes) + len(plan.remove)`, `done` cumulative over both. Calls: `('downloading', 0, total)` before the first write, after each write attempt (skipped ones count) `('downloading', i, total)`, `('removing', len(writes), total)` before removals when any, after each removal attempt `('removing', n, total)`.
- Produces: `LightroomManager.public()` gains `phase`, `done`, `total` (None outside a sync) and `progress` (int percent or None).
- Produces: `LightroomProgressSensor`, unique id `f'{entry.entry_id}_lightroom_progress'`, name `DomoSenso Photos Lightroom sync progress`, unit `%`, icon `mdi:progress-download`, state `public()['progress']`.

- [ ] **Step 1: Failing unit tests** in `tests/test_lightroom_sync.py`:
  - `test_execute_reports_progress`: a plan with 3 adds and 1 remove (built with the file's helpers), a recording callback; assert calls equal `[('downloading',0,4),('downloading',1,4),('downloading',2,4),('downloading',3,4),('removing',3,4),('removing',4,4)]`.
  - `test_execute_progress_counts_skipped`: a fetch returning `None` for one of 2 adds; last call `('downloading',2,2)`.
  - `test_execute_progress_nothing_to_do`: empty plan; calls `[]`.
  - `test_execute_without_progress`: existing call style (no callback) still works (covered by existing tests; no new test needed if they pass).
- [ ] **Step 2:** Run `.venv/bin/python -m pytest -q tests/test_lightroom_sync.py` — the three new tests FAIL (unexpected keyword `progress`).
- [ ] **Step 3: Implement** the `progress` parameter in `execute` as in Interfaces.
- [ ] **Step 4: Manager.** In `LightroomManager`: `self.progress = {'phase': None, 'done': None, 'total': None}` and `self._progress_sent = 0.0`. Method `_report(phase, done, total)`: updates the dict; sends `signal(entry_id)` when the phase changed, `done == total`, or ≥ 5 s (`time.monotonic()`) since the last send. `_sync_body` calls `_report('reading_library', 0, 0)` before `read_library` and passes `self._report` to `execute`. `_sync_locked` resets the dict to all-`None` before saving (every outcome). Remember `self._last_run_complete`: True only when the update status is `ok` (in memory; on load: `bool(self.data.get('last_success'))`). `public()['progress']`: while the lock is held: `None` in `reading_library` or when `total` is None, `100` when `total == 0`, else `done * 100 // total`; outside a sync: `100` if `_last_run_complete` else `None`.
- [ ] **Step 5: Sensors.** Add `'phase', 'done', 'total'` to `LIGHTROOM_ATTRIBUTES`; add `LightroomProgressSensor` (same dispatcher wiring as `LightroomSensor`, `native_unit_of_measurement='%'`, `state_class` none) and add it next to `LightroomSensor` in `async_setup_entry`.
- [ ] **Step 6: Smoke.** In `tests/lightroom_ha_smoke.py`, around the first Lightroom sync: record every state of the Lightroom sensor (`async_track_state_change_event`); after the sync assert some recorded state had `phase == 'downloading'` and `total > 0`, the final state has `phase is None`, and the progress sensor's state is `'100'`.
- [ ] **Step 7:** Unit tests all pass; the HA smoke prints `lightroom smoke ok`; the albums smoke prints `albums smoke ok`.
- [ ] **Step 8: Docs.** README: the three attributes and the progress sensor in the Lightroom sensor section. CHANGELOG `## Unreleased`: "Lightroom: progress while syncing (sensor attributes `phase`, `done`, `total`; sensor *Lightroom sync progress* in %)."
- [ ] **Step 9: Commit** `Lightroom: show the sync's progress`.

### Task 2: Change the folder in Settings

**Files:**
- Modify: `custom_components/domosenso_photos/config_flow.py` (`async_step_lightroom_settings`, new `async_step_lightroom_folder_confirm`)
- Modify: `custom_components/domosenso_photos/lightroom.py` (`_options_changed`)
- Modify: `strings.json`, `translations/{en,de,pl}.json`
- Test: `tests/lightroom_ha_smoke.py`
- Docs: `README.md`, `CHANGELOG.md`

**Interfaces:**
- Consumes: `_photo_dir(hass, value)`, `_prefix(value)` (config_flow.py), `LightroomManager.lock`, `.photo_dir`, `.prefix`.
- Produces: options step ids `lightroom_settings` (fields `photo_dir`, `prefix` added first, defaults from `entry.data`) and `lightroom_folder_confirm` (no fields; placeholders `old` and `new`, each the full folder path, prefix joined). Error keys `invalid_photo_dir`, `invalid_prefix` (existing), `sync_running` (new).

- [ ] **Step 1: Failing smoke checks** in `tests/lightroom_ha_smoke.py`, after the existing settings checks:
  - Settings submitted with the same folder and prefix: result `create_entry`, no `lightroom_folder_confirm`.
  - While holding `entry.runtime_data.lock` (`async with`): Settings with a new existing folder → form `lightroom_settings` with `errors == {'base': 'sync_running'}`; `entry.data['photo_dir']` unchanged.
  - Settings with a new folder `<tmp>/lr2` (created) and `interval_min` changed → form `lightroom_folder_confirm` whose `description_placeholders` hold the old and new paths; submit `{}` → `create_entry`; `entry.data['photo_dir']` is the new folder, the interval saved; after the reload's sync (`synced(...)`) the new folder holds the expected files and the old folder's files are byte-identical to before.
  - Settings with an invalid folder → `errors['photo_dir'] == 'invalid_photo_dir'`.
- [ ] **Step 2:** Run the HA smoke — FAILS at the first new check.
- [ ] **Step 3: Implement.** `async_step_lightroom_settings`: validate `photo_dir`/`prefix` as `async_step_lightroom_folder` does; compute the new options as today. If folder and prefix are equal to `entry.data`: `async_create_entry(data=new_options)` (today's path). Else, if the manager's lock is held: show the form again with `errors={'base': 'sync_running'}`; else keep `self._pending = (path, prefix, new_options)` and go to `async_step_lightroom_folder_confirm`. The confirm step shows its form with `old`/`new`; on submit, re-check the lock (same error, back to `lightroom_settings`), then `hass.config_entries.async_update_entry(entry, data={**entry.data, 'photo_dir': path, 'prefix': prefix}, options=new_options)` and `return self.async_create_entry(data=new_options)`. `_options_changed`: reload also when `entry.data['photo_dir']` or `entry.data.get('prefix') or ''` differs from the manager's `photo_dir`/`prefix`.
- [ ] **Step 4: Texts** (all four files). `lightroom_settings` description becomes: "Changing the photo folder or the subfolder asks for a confirmation first." Data labels `photo_dir` "Photo folder", `prefix` "Subfolder". `lightroom_folder_confirm`: title "Change the photo folder", description "Photos will be synced into {new}. Every photo is downloaded again, unless that folder already holds this library's photos. The photos in {old} stay where they are: the integration does not delete them. Submit to change the folder." Error `sync_running`: "A sync is running. Try again when it has finished." The options flow's own `error` block gets `sync_running`, `invalid_photo_dir` and `invalid_prefix` (the last two today exist only in the config flow's block; copy their texts). Translate into de and pl in the files' existing register.
- [ ] **Step 5:** Unit tests pass; `lightroom smoke ok`; `albums smoke ok`.
- [ ] **Step 6: Docs.** README: the Settings step's two fields and what a change does (old folder left alone). CHANGELOG `## Unreleased`: "Lightroom: the photo folder and subfolder can be changed in Settings; the photos are downloaded again into the new folder and the old folder is left as it is."
- [ ] **Step 7: Commit** `Lightroom: change the photo folder in Settings`.
