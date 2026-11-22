# Lightroom as a second photo source: design

Date: 2026-11-22. Status: draft for approval.

## Intent

DomoSenso Photos fills a photo folder that a slideshow reads. Today the only source is a Synology
Photos share link. This adds **Adobe Lightroom (cloud)**: the photos of a Lightroom library that
pass a filter the user sets (stars, flag, albums, dates, and more), edited as in Lightroom, a
folder per album, kept in step without anyone touching it.

Why: a working stand-alone container already does this for one home (rating ≥ 3, one folder per
album). Moving it into the integration gives every user the same feature, with Home Assistant's
own sign-in, repair issues and sensor, and lets that container be retired later. Users also want to
choose **which** photos reach the frame by more than stars; the filter is part of the feature, not
an extra.

## Goals

1. Connect a Lightroom account through Home Assistant's standard OAuth (Application Credentials):
   the user brings their own Adobe Developer Console client id and secret.
2. Select photos by a **filter** evaluated on metadata before any image is downloaded (below).
3. Put each selected photo in one folder per Lightroom album; photos in no album in month folders.
4. The Lightroom source only **selects and fetches**: it picks photos by the filter and fetches
   Adobe's rendition (edits applied). Turning that into the file on disk is the integration's
   existing image pipeline (`album_image.normalize`), the same as for Synology: longest edge
   (`max_edge`), JPEG quality, orientation, GPS removed. The source adds the EXIF the rendition
   lacks, from the asset's metadata (date, camera, lens, exposure). Never location.
5. Re-download a photo when it is edited in Lightroom; move it when its album changes; remove it
   when it no longer passes the filter or leaves the library.
6. The same safety as the Synology source: only files the integration wrote are ever touched, and
   a large removal waits for confirmation through a repair issue.
7. A **preview** of a filter change (what would be added and removed, per folder) before it is
   saved.

## Non-goals

- Lightroom Classic (desktop catalogs), Adobe Portfolio, other Adobe products.
- Videos.
- Image settings of its own: resolution and quality are the entry's existing **Longest edge** and
  **JPEG quality** settings, applied by the shared pipeline.
- Filtering by people (face names): face data is personal and not needed for a first version.
- A year/month folder layout for Lightroom (the Synology source keeps its own layout).
- Taking over files written by another tool (see "Coexistence"). A one-off migration of a home's
  existing files is that home's business.
- Incremental listing with `updated_since`: a full read per run is fast enough at thousands of
  photos.

## Anti-goals (a failure even if it works)

- A filter change, an Adobe outage or a changed API that empties the frame without the user
  confirming it.
- Touching a file or folder the integration did not write.
- Writing GPS, the camera serial number or face regions into a file under `/config/www`.
- Secrets anywhere but Home Assistant's config entry storage.
- **Any write to the Lightroom cloud.** The integration only reads: every request to
  `lr.adobe.io` is a `GET`; the only `POST` goes to Adobe's token endpoint (sign-in and refresh).
  No photo, album, rating, flag or rendition is ever created, changed or deleted there. Adobe's
  Lightroom scope is not read-only (inference: no read-only scope is documented), so this is
  enforced in the client: it refuses any other method before sending.

## Constraints

- Home Assistant 2026.9.2 or newer; HACS custom integration; no new Python requirements (Pillow
  and aiohttp ship with Home Assistant).
- Adobe's API as recorded in `docs/lightroom-api.md` (to be written from the existing probe notes,
  with invented example values). It is a partner API; Adobe may change it.
- The refresh token lives about 14 days. It has been seen to renew itself when a refresh happens
  while it is about a week old (inference from one account's history). A daily sync keeps it
  alive; a lapse becomes Home Assistant's re-authentication.
- The repository is public: no names, paths, addresses or albums of any real home.

## Approaches considered

- **A. A whole library behind one filter (chosen).** One Lightroom account is one source; a
  filter selects photos across the whole library; folders follow albums automatically. It matches
  what runs today, makes a later switch from the container mechanical, and "only these albums" is
  just one filter condition.
- **B. Albums picked one by one,** like Synology share links. Each album gets its own status and
  hold, but a photo in two albums appears twice, photos in no album cannot be reached, and every
  new Lightroom album needs a trip to the options. Dropped: the filter in A covers it.
- **C. Keep a separate container and only read its status.** No change to the integration; does
  not give other users the feature or the filter. Dropped.

## Design

### Entries

- `single_config_entry` is dropped. The integration has two kinds of entry, chosen in the first
  step of **Add integration**:
  - **Synology albums**: as today, unique id `albums` (still at most one).
  - **Lightroom library**: an OAuth2 entry (`AbstractOAuth2FlowHandler`), unique id the Adobe
    account id (`sub` of the ID token, else of the access token), so one entry per Adobe account.
- Each entry has its own `photo_dir`. Two entries may share one folder: markers keep their folders
  apart (existing `resolve_path` logic: a folder owned by someone else gets a `-2` suffix and a
  repair issue).
- Removing a Lightroom entry deletes its token (entry data) and its catalog cache; its photo
  folders stay, as with Synology. Folders are owned by the **Adobe account**, not the entry
  (below), so removing and adding the same account again takes its folders back instead of
  downloading a second copy beside them. *(Builder-check call.)*

### Sign-in

- `application_credentials.py`: authorize
  `https://ims-na1.adobelogin.com/ims/authorize/v2`, token `https://ims-na1.adobelogin.com/ims/token/v3`,
  scopes `openid,AdobeID,lr_partner_apis,lr_partner_rendition_apis,offline_access`.
- The user registers Home Assistant's redirect `https://my.home-assistant.io/redirect/oauth` in
  their Adobe project (OAuth Web App, Lightroom Services). README walks through it.
- Every Lightroom call sends `X-API-Key: <client id>` and the bearer token from
  `OAuth2Session.async_ensure_token_valid()`. Home Assistant stores a renewed refresh token.
- A refused refresh, or a 401/403 from Lightroom after a fresh token, raises
  `ConfigEntryAuthFailed`: Home Assistant shows its re-authentication flow. A 401/403/404, or a
  2xx that is not a JPEG (for example a rendition still being generated), on a single rendition
  skips that photo instead (counted, retried after a day). *(Builder-check call.)*
- **Before it lapses:** a repair issue `signin_expiring` appears when `signin_expires` is less than
  3 days away, and goes when a refresh moves it on. Renewal on refresh has been seen on one account
  (inference), not documented by Adobe; the issue is the safety net. *(Builder-check call.)*
- **Fallback if Adobe refuses the `my.home-assistant.io` redirect** (open item 1): the flow uses a
  fixed `https://localhost/...` redirect and a step where the user pastes the address of the page
  that does not load; the code is read from it (state checked). This is how the existing container
  signs in, with no certificate and no remote access. Only one of the two ships, decided by the
  first build task. *(Builder-check call.)*

### Reading the library

- `GET /v2/catalog` (id), all image assets (`/assets?subtype=image&limit=100`, paged by
  `links.next`), all albums (`/albums`, `collection` and `collection_set` with their `parent`), and
  each album's asset ids. Responses start with `while (1) {}`; strip it.
- Throttle 0.2 s between calls; 429 and 5xx retried with back-off 2, 4, 8 s (honour `Retry-After`);
  then the run fails with `unreachable` and changes nothing.
- Each asset becomes a `Photo` (asset id, rating, flag, capture time as camera wall time, edited
  time, make, model, lens, focal, focal 35 mm, aperture, exposure, ISO, album ids, and — if the
  probe confirms the fields — width, height, keywords). Rating and flag: the latest `date` among
  apps wins.
- The trimmed photo list and the album tree are saved per entry in a `Store`
  (`domosenso_photos.lightroom.<entry id>`). The preview and the album picker read it.
- **Nothing in the photo folder is written until the whole library has been read.**

### The filter

All set conditions must hold (AND). Empty means "no condition".

| setting | values | default |
|---|---|---|
| `ratings` | any subset of 0 (unrated) – 5 | 3, 4, 5 |
| `flag` | `any`, `not_rejected`, `picks_only` | `not_rejected` |
| `albums_include` | album or album-set ids; a set means every album below it | empty (all) |
| `albums_exclude` | album or album-set ids; wins over include | empty |
| `include_unfiled` | photos in no album | true |
| `captured_from`, `captured_to` | dates, inclusive | empty |
| `captured_last_years` | 1–50; replaces from/to when set | empty |
| `edited_only` | true: only photos changed from Lightroom's defaults (`develop.fromDefaults` is false) | false |
| `equipment_include`, `equipment_exclude` | text; matches, case-insensitive, as a substring of make, model or lens | empty |
| `orientation` | `any`, `landscape`, `portrait` | `any` — **only if the probe finds dimensions** |
| `keywords_include`, `keywords_exclude` | text list, case-insensitive, whole keyword | empty — **only if the probe finds keywords** |

- With `albums_include` set, a photo passes only through a non-excluded included album; it is then
  placed in one of those albums. With it empty, any non-excluded album places it. A photo whose
  every album is excluded is dropped (it does not fall back to a month folder). A photo in no album
  passes only if `include_unfiled`.
- The filter lives in the entry options, edited in **Configure → Filter**: album fields use a
  multi-select of the cached album tree (labels `Set / Album`); an album id that no longer exists is
  kept, shown as "missing album", and ignored.
- **Preview**: the Filter step's submit goes to a confirmation step that shows, from the cache,
  photos kept, to add, to remove, and the five folders with the most change, and the cache's date.
  Save applies the options (entry reload, then a sync); Back returns to the form.
  A `lightroom_preview` action returns the same counts for the current options.

### Folders and files

> Amended 2026-11-22: albums land at their path in the album tree, see
> `2026-11-22-lightroom-album-tree-design.md`.

- Folder: `<prefix>/<album name>`; photos in no album: `<prefix>/<YYYY-MM>` of the capture date.
  `prefix` is set at setup with `photo_dir`, default empty (folders directly in `photo_dir`), and
  is not changed afterwards: changing it would move every photo. *(Builder-check call.)*
- A photo in several eligible albums goes to the first by name (case-insensitive, then by id).
- Album names are cleaned with the existing `sanitize_name`, and additionally lose a leading `@`
  (slideshows commonly skip `.`/`@` folders, e.g. a NAS's `@eaDir`). Two albums whose cleaned
  names collide: the second by id gets `-2` (existing `resolve_path`).
- File: `<YYYY-MM-DD>_<asset id>.jpg`, written via a dot-named temp file and `os.replace`; mtime =
  capture time.
- **Which rendition:** `2048` when `max_edge` ≤ 2048. Otherwise `fullsize` **if Adobe already
  has it** (a `GET`; it exists when Lightroom generated it before), else `2048`, and the photo is
  simply smaller than `max_edge` (the pipeline never enlarges). The integration never asks Adobe
  to generate a rendition: that is a `POST` that adds a file to the cloud (read-only rule).
- **Transformation:** the fetched bytes go through `album_image.normalize(data, max_edge, quality)`
  like a Synology original. The Lightroom source passes the EXIF tags it built from the asset's
  metadata (DateTime, DateTimeOriginal as capture wall time, Make, Model, LensModel, FocalLength,
  FocalLengthIn35mmFilm, FNumber, ExposureTime, ISO) so they are written in the same single
  encode; `normalize` gains that optional argument. Nothing else is written.
- **ICC profile:** `normalize` today drops the source's ICC profile, so wide-gamut photos (Lightroom
  renditions; also iPhone originals in the Synology source) shift in colour. `normalize` keeps
  it; this fixes the Synology source too and goes in the CHANGELOG.
- A rendition that cannot be fetched or decoded is skipped and counted (`skipped_count`), retried
  after a day (see Sign-in for the HTTP cases).

### Ownership and changes

- Each Lightroom folder gets the usual `.sync.json` marker: `{"owner": "lightroom:<first 16 hex of sha256(account id)>",
  "kind": "lightroom", "items": {"<asset id>": {"name": "...", "changed": "..."}}, "last_sync": ...}`.
  The Synology marker keeps `album_id`; `resolve_path`/`_owned` learn to read `owner` too.
- The source record (in the entry's Store) lists its folders: album id (or `YYYY-MM`) → path.
- A run computes, from the filter result and the markers: add, update (`changed` differs), move
  (folder differs), remove (no longer selected or gone). A move writes the new file and marker
  first, then removes the old file and updates the old marker. A folder left empty with only its
  marker loses both. A renamed album renames its folder (as the Synology source does).

### Safety holds

- A run holds (removes nothing, moves nothing, adds the rest) and sets `awaiting_confirmation` with a
  repair issue when, counting files the source owns (at least 20):
  - more than half would be removed, or
  - more than half would move to month folders (an empty or broken album listing), or
  - the library listing returns no assets.
- Confirmed by **Sync now → Confirm held changes** or `lightroom_sync` with `confirm: true`, for a
  hold that exists when the call is made (existing rule).
- A filter change that removes more than half therefore asks for confirmation after Save: the
  preview already warned.

### Schedule, status, sensor, actions

- Sync every `interval_min` (default 360, range 60–1440), 60 s after start, after Save, and on
  demand. One sync at a time per entry; Synology and Lightroom entries sync independently.
- Status: `ok`, `syncing`, `awaiting_confirmation`, `unreachable`, `disk_full`, `error` (codes as
  today plus `unexpected_response`), and re-authentication through Home Assistant.
- Sensor `sensor.domosenso_photos_lightroom_<n>` per entry: state = status; attributes
  `photos`, `folders`, `last_sync`, `last_success`, `added`, `updated`, `moved`, `removed`,
  `skipped_count`, `error`, `signin_expires` (from the refresh token's claims, when readable),
  `cache_date`. Not recorded: `folders`.
- Actions (admin only, with response): `lightroom_sync` (`entry_id` optional, `confirm`),
  `lightroom_preview` (`entry_id`). The filter is not set through actions.
- Options **Settings**: `photo_dir` and `prefix` are fixed at setup; `interval_min`, `free_floor`.
- Diagnostics (download from the entry, redacted): counts of assets carrying each metadata field
  (rating, flag, capture date, make, lens, dimensions, keywords …), never values. This is also how
  the orientation and keyword fields get confirmed on a real account.

### Coexistence

The integration never writes into a folder without its marker, but it cannot know about another
tool writing the same photo folder: its folders would get `-2` suffixes next to the other tool's.
README says: use a separate `photo_dir` (or a `prefix`) until the other tool is stopped and its
files are removed.

### Code layout

New modules, HA-free where marked, beside the existing ones:

| module | holds |
|---|---|
| `lightroom_api.py` (HA-free) | async client on the existing transport pattern: paging, prefix, throttle, retries, rendition |
| `lightroom_catalog.py` (HA-free) | asset JSON → `Photo`; album tree |
| `lightroom_filter.py` (HA-free) | filter options → predicate; folder assignment |
| `lightroom_exif.py` (HA-free) | asset metadata → EXIF tags for `normalize` |
| `album_image.py` (changed) | `normalize` takes extra EXIF tags and keeps the ICC profile |
| `lightroom_sync.py` (HA-free) | plan (add/update/move/remove/hold) and its execution on markers |
| `lightroom.py` | entry manager: OAuth session, timer, store, issues, services |
| `application_credentials.py`, `diagnostics.py` | HA platforms |

`config_flow.py` gains the kind menu, the OAuth steps, reauth and the Lightroom options; strings
in en, de, pl. `sensor.py` adds the Lightroom sensor.

### Tests

- Unit tests for every HA-free module, with invented fixtures shaped like Adobe's JSON (the
  catalog, filter and sync tests are where the existing container's 71 tests carry over).
- `tests/lightroom_ha_smoke.py`: Home Assistant 2026.9.2 in Docker, a fake IMS and Lightroom on
  `127.0.0.1` (authorize/token URLs patched for the test), covering the flows, reauth, filter and
  preview, holds, sensor, actions, unload and removal.

## Left to the builder

Names of private helpers, the exact back-off code, translation wording, how the preview lays out
its counts, and where the shared marker code is factored.

## Open before building

These were checked first, on a real account. Items 1 and 3–5 were checked on 2026-11-22 (item 2 is still open); the
results are in `docs/lightroom-api.md` (Decision record), which is the authority:

1. Adobe accepts `https://my.home-assistant.io/redirect/oauth` as a redirect for the Web App. If
   not, the paste fallback above.
2. One Adobe client can hold two live sign-ins (the old tool's and Home Assistant's) — inference.
   If not, the old tool stops before the prototype.
3. Orientation and keyword filters: confirmed by the diagnostics; if a field is absent, that filter
   is left out of the first release.
4. `payload.develop.userUpdated` changes with each edit (inference in the probe notes): checked by
   editing one photo and reading it again. If not, `changed` combines it with `payload.userUpdated`
   and the asset's `updated`. `edited_only` means: `develop.fromDefaults` is false (when absent,
   `develop.userUpdated` present).
5. How often a `fullsize` rendition already exists (a `GET`, nothing generated). If rarely, README
   says Lightroom photos are at most 2048 px in practice.
