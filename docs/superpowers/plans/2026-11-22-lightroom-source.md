# Lightroom Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A second entry kind, "Lightroom library", that signs in to Adobe through Home Assistant's
OAuth, selects photos with a user filter, and syncs them into one folder per album through the
existing image pipeline.

**Architecture:** HA-free modules do the work (catalog parsing, filter and placement, EXIF tags,
an async Lightroom client on the existing transport pattern, plan and execution on `.sync.json`
markers); `lightroom.py` is the entry manager (OAuth session, timer, store, issues, services); the
config flow gains a kind menu, the OAuth steps, reauth and the Lightroom options with a preview.
The Synology code path changes only where it is shared: `album_image.normalize` (ICC profile,
extra EXIF), marker ownership in `album_layout`, and the transport moved to its own module.

**Tech Stack:** Python 3.14, Home Assistant 2026.9.2 (config entries, `config_entry_oauth2_flow`,
`application_credentials`, `Store`, issue registry, diagnostics), aiohttp, Pillow; pytest.

**Spec:** `docs/superpowers/specs/2026-11-22-lightroom-source-design.md`

## Global Constraints

- Home Assistant 2026.9.2 or newer; no new entries in `manifest.json` `requirements`.
- `manifest.json`: remove `"single_config_entry": true`; add `"dependencies": ["application_credentials"]`; keep `"version": "0.1.2"` (the release number is the maintainer's call, not this plan's).
- No HA imports in `lightroom_api.py`, `lightroom_catalog.py`, `lightroom_filter.py`, `lightroom_exif.py`, `lightroom_sync.py` (they are tested by plain pytest like `album_sync.py`).
- Tests import modules the way `tests/test_album_sync.py` does (a synthetic package whose `__path__` is `custom_components/domosenso_photos`).
- All fixtures and examples are invented: no real names, albums, paths, addresses or ids.
- Never write GPS, the camera serial number or face regions into a photo file.
- Secrets only in the config entry (HA's OAuth token storage); none in markers, the store, the sensor, diagnostics or logs.
- Scopes `openid,AdobeID,lr_partner_apis,lr_partner_rendition_apis,offline_access`; authorize `https://ims-na1.adobelogin.com/ims/authorize/v2`; token `https://ims-na1.adobelogin.com/ims/token/v3`; API `https://lr.adobe.io`; every API call sends `X-API-Key: <client id>`.
- Strings in `strings.json`, `translations/en.json`, `de.json`, `pl.json` stay in step (same keys).
- Run `python -m pytest -q tests` green at the end of every task.
- **Read-only towards Adobe.** Every request to `lr.adobe.io` is a `GET`; the only `POST` is to `TOKEN_URL`. `LightroomClient` raises `ValueError('read-only')` before sending any other method, and nothing in the integration generates renditions.
- **Implementers and reviewers never get Adobe access.** Tasks 2–11 run against fakes only; no subagent is given a client id, secret, token or the user's sign-in, and none calls `lr.adobe.io` or `ims-na1.adobelogin.com`. Only Task 1 (coordinator, user signs in) and Task 12 (the user's Home Assistant) touch Adobe, and only with `GET`s plus the token exchange.

## Review Focus

1. **A sync stopped between a move's write and its removal** (HA restart): the next run finds the asset in two owned folders and must keep exactly the one the filter places it in — Task 7 `test_plan_asset_in_two_owned_folders_removes_stale_copy`.
2. **Album names that clean to nothing or to the same folder** (only symbols, a leading `@`, two albums "Trip" and "Trip/"): a stable folder each, the second by id with `-2`, the same on every run — Task 4 `test_folder_name_only_symbols_is_album`, Task 7 `test_two_albums_same_clean_name_stable_suffix`.
3. **Filter options naming albums that were deleted in Lightroom**: ignored, no crash; an include list whose albums are all gone lets no album photo through — Task 4 `test_include_with_only_missing_ids_passes_no_album_photo`.
4. **Photo folder unmounted at sync time**: status `error`/`photo_dir_missing`, nothing written, nothing removed — Task 7 `test_execute_photo_dir_missing_touches_nothing`.
5. **The library listing comes back empty or without albums** (an Adobe hiccup): no photo removed and none moved into month folders until confirmed — Task 7 `test_plan_empty_library_holds`, `test_plan_mass_unfile_holds`.

---

### Task 1: Gate — confirm the open facts on a real account

No product code. The results decide Task 6 (rendition), Task 8 (redirect) and which filter fields Task 3 parses.

**Files:**
- Create: `docs/lightroom-api.md` (what the API returns; invented example values only)

- [ ] **Step 1:** Write a throwaway probe in the session's scratch directory (not in the repo) that signs in with the user's own Adobe client (redirect `https://localhost/...`, the user pastes the address), then **GET only**: catalog, first 200 assets, albums, one album's assets. The probe's HTTP helper refuses any method but `GET` towards `lr.adobe.io`. The user does the sign-in.
- [ ] **Step 2:** Record field coverage over the 200 assets (counts, no values) for: `payload.ratings`, `payload.reviews`, `payload.captureDate`, `payload.develop.userUpdated`, `payload.xmp.tiff.Make`, `payload.xmp.aux.Lens`, any width/height fields (look under `payload.importSource` and `payload.develop`), any keyword fields (look for `xmp.dc.subject`, `payload.keywords`).
- [ ] **Step 3:** Ask the user to edit one photo in Lightroom; read that asset again; record whether `payload.develop.userUpdated` changed.
- [ ] **Step 4:** For 20 assets: `GET .../renditions/fullsize` once each (nothing generated); record how many answer 200, their pixel size, and whether EXIF/ICC are present.
- [ ] **Step 5:** Decode the token response: record which claim gives a stable account id (`id_token` `sub`, or the access token's `user_id`) and the refresh token's `created_at`/`expires_in`.
- [ ] **Step 6:** In the Adobe Developer Console the user adds `https://my.home-assistant.io/redirect/oauth` to the Web App's redirect patterns; record whether the console accepts it.
- [ ] **Step 7:** After the next run of any other tool using the same client, record that its sign-in still works (two live sign-ins).
- [ ] **Step 8:** Write `docs/lightroom-api.md`: endpoints, headers, `while (1) {}` prefix, paging by `links.next`, the asset field table with the confirmed paths, rendition behaviour, token claims — each finding marked read or inference. Add a "Gate results" list answering spec open items 1–5 and the account-id claim.
- [ ] **Step 9:** Commit `docs/lightroom-api.md` ("Lightroom API notes from a real-account probe"). Delete the scratch probe.

**Decision record for later tasks** (fill in at Step 8, top of `docs/lightroom-api.md`):
`REDIRECT = my | paste`, `FULLSIZE = often | rarely`, `DIMENSIONS = <path> | none`, `KEYWORDS = <path> | none`, `CHANGED = develop.userUpdated | combined`, `ACCOUNT_CLAIM = <claim>`.

---

### Task 2: Shared image pipeline keeps ICC and takes extra EXIF

**Files:**
- Modify: `custom_components/domosenso_photos/album_image.py` (`normalize`, `_exif_candidates` callers)
- Modify: `CHANGELOG.md` (new "Unreleased" section)
- Test: `tests/test_album_image.py`

**Interfaces:**
- Produces: `normalize(data: bytes, max_edge: int, quality: int, add_exif: Callable[[Image.Exif], None] | None = None) -> bytes`. `add_exif` is called on the source's `Exif` before orientation reset and GPS removal. The output carries the source's `icc_profile` when it has one.

- [ ] **Step 1: Write the failing tests**

```python
def test_normalize_keeps_icc_profile():
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    src = _jpeg((400, 300), icc=icc)          # extend _jpeg with an icc kwarg
    out = album_image.normalize(src, 200, 85)
    assert Image.open(io.BytesIO(out)).info.get('icc_profile') == icc

def test_normalize_without_icc_has_none():
    out = album_image.normalize(_jpeg((400, 300)), 200, 85)
    assert 'icc_profile' not in Image.open(io.BytesIO(out)).info

def test_normalize_add_exif_written():
    def add(ex): ex[0x010F] = 'Maker X'; ex.get_ifd(0x8769)[0x8827] = 200
    out = album_image.normalize(_jpeg((400, 300), exif=False), 200, 85, add)
    ex = Image.open(io.BytesIO(out)).getexif()
    assert ex[0x010F] == 'Maker X' and ex.get_ifd(0x8769)[0x8827] == 200

def test_normalize_add_exif_gps_still_removed():
    def add(ex): ex.get_ifd(0x8825)[1] = 'N'
    out = album_image.normalize(_jpeg((400, 300)), 200, 85, add)
    assert not Image.open(io.BytesIO(out)).getexif().get_ifd(0x8825)
```

- [ ] **Step 2:** Run `python -m pytest -q tests/test_album_image.py` — the four new tests FAIL.
- [ ] **Step 3:** Implement: call `add_exif(exif)` right after `src.getexif()`; pass `icc_profile=src.info.get('icc_profile')` (read before the source closes) to every `img.save` when present.
- [ ] **Step 4:** Run `python -m pytest -q tests` — all PASS.
- [ ] **Step 5:** CHANGELOG "Unreleased": "Photos keep their colour profile (ICC); wide-gamut photos, such as iPhone originals, no longer shift in colour."
- [ ] **Step 6:** Commit ("Keep the ICC profile and accept extra EXIF in normalize").

---

### Task 3: Lightroom catalog model

**Files:**
- Create: `custom_components/domosenso_photos/lightroom_catalog.py`
- Create: `tests/fixtures/lightroom/asset_full.json`, `asset_bare.json`, `albums.json` (invented, shaped as `docs/lightroom-api.md`)
- Test: `tests/test_lightroom_catalog.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) CameraMeta(make, model, lens: str|None, focal_mm, focal35_mm, f_number, exposure_s: float|None, iso: int|None)`
  - `@dataclass(frozen=True) Photo(asset_id: str, rating: int, flag: str|None, captured: datetime, changed: str, edited: bool, album_ids: tuple[str, ...], meta: CameraMeta, width: int|None = None, height: int|None = None, keywords: tuple[str, ...] = ())` — `captured` naive (camera wall time); `flag` is `'pick'`, `'reject'` or `None`; `rating` 0 when unrated.
  - `@dataclass(frozen=True) Album(id: str, name: str, parent_id: str|None, is_set: bool)`
  - `to_photo(asset: dict, album_ids: Sequence[str]) -> Photo`
  - `to_album(resource: dict) -> Album` (`subtype == 'collection_set'` → `is_set`)
  - `album_label(album_id: str, albums: dict[str, Album]) -> str` (`"Set / Sub / Album"`)
  - `photo_to_dict(p: Photo) -> dict`, `photo_from_dict(d: dict) -> Photo` (JSON-safe, for the cache)

- [ ] **Step 1: Write the failing tests** (values from the invented fixtures)
  - `test_to_photo_full`: rating 4, flag `'pick'`, `captured == datetime(2024, 5, 3, 17, 3, 26)` from `"2024-05-03T17:03:26.84+02:00"`, make/model/lens strings, `focal_mm == 35.0` from `[35, 1]`, `f_number == 1.8` from `[18, 10]`, `exposure_s == 0.0025` from `[1, 400]`, iso 200, `edited is True`, `changed` == `develop.userUpdated`.
  - `test_to_photo_latest_rating_wins`: two app entries, the later `date` (rating 2) wins over rating 5.
  - `test_to_photo_bare`: no ratings → 0, no reviews → `None`, no captureDate → import timestamp, no develop → `edited is False` and `changed` falls back to `payload.userUpdated`, then `updated`.
  - `test_to_photo_dimensions_and_keywords`: only if the Task 1 record found the paths; else assert `width is None and keywords == ()`.
  - `test_to_album_set_and_collection`, `test_album_label_nested` (`"Travel / 2024 / Rome"`), `test_photo_dict_round_trip`.
  - With `CHANGED = combined` in the Task 1 record: `changed` is `f"{develop.userUpdated}|{payload.userUpdated}|{updated}"`; the fixture test asserts that string.
- [ ] **Step 2:** Run `python -m pytest -q tests/test_lightroom_catalog.py` — FAIL (module missing).
- [ ] **Step 3:** Implement. Rating/flag: the entry with the greatest `date` among `payload.ratings.*` / `payload.reviews.*`. Rationals `[n, d]` → `n/d`. Capture time: `datetime.fromisoformat(v).replace(tzinfo=None, microsecond=0)`, fallbacks `importSource.importTimestamp`, `userCreated`, `created`.
- [ ] **Step 4:** Run the tests — PASS.
- [ ] **Step 5:** Commit ("Lightroom catalog model").

---

### Task 4: Filter and placement

**Files:**
- Create: `custom_components/domosenso_photos/lightroom_filter.py`
- Test: `tests/test_lightroom_filter.py`

**Interfaces:**
- Consumes: `Photo`, `Album` (Task 3); `sanitize_name` (`album_layout.py`).
- Produces:
  - `@dataclass(frozen=True) Filter(ratings: frozenset[int] = frozenset({3, 4, 5}), flag: str = 'not_rejected', albums_include: frozenset[str] = frozenset(), albums_exclude: frozenset[str] = frozenset(), include_unfiled: bool = True, captured_from: date|None = None, captured_to: date|None = None, captured_last_years: int|None = None, edited_only: bool = False, equipment_include: tuple[str, ...] = (), equipment_exclude: tuple[str, ...] = (), orientation: str = 'any', keywords_include: tuple[str, ...] = (), keywords_exclude: tuple[str, ...] = ())`
  - `filter_from_options(options: dict) -> Filter`, `filter_to_options(f: Filter) -> dict` (JSON-safe: lists, ISO dates)
  - `@dataclass(frozen=True) Placed(photo: Photo, key: str, folder: str)` — `key` is the album id or `'YYYY-MM'`; `folder` is the wanted path relative to `photo_dir` (prefix included).
  - `folder_name(name: str) -> str` — `sanitize_name`, then leading `@` stripped, then `'album'` if empty.
  - `place(photos: Iterable[Photo], f: Filter, albums: dict[str, Album], prefix: str, today: date) -> list[Placed]`

Rules (pin them in tests): every set condition must hold. `ratings`: `photo.rating in f.ratings`. `flag`: `not_rejected` → `flag != 'reject'`; `picks_only` → `flag == 'pick'`. Dates compare `captured.date()`, inclusive; `captured_last_years = n` replaces from/to with `from = today` minus n years (29 Feb → 28 Feb), no upper bound. `edited_only` → `photo.edited`. Equipment: a term matches when it is a casefolded substring of make, model or lens; include needs one match, exclude drops on one match. Keywords: whole keyword, casefolded. Orientation: `landscape` → `width > height`, `portrait` → `height > width`; unknown size or square fails both. Albums: set ids expand to every collection below them; ids not in `albums` are ignored; eligible = photo's albums ∩ (expanded include, or all collections when include is empty) − expanded exclude. A photo with albums but no eligible one is dropped. A photo in no album passes only if `include_unfiled`, into key `captured.strftime('%Y-%m')`. Among eligible albums: first by `(name.casefold(), id)`. `folder = f'{prefix}/{name}'` or `name` when prefix is empty.

- [ ] **Step 1: Write the failing tests** — one per rule above, plus:
  - `test_default_filter_is_three_stars_not_rejected`
  - `test_exclude_wins_over_include`
  - `test_set_in_include_expands_to_children`
  - `test_photo_only_in_excluded_album_is_dropped_not_unfiled`
  - `test_include_with_only_missing_ids_passes_no_album_photo` (Review Focus 3)
  - `test_last_years_on_feb_29`
  - `test_folder_name_only_symbols_is_album`, `test_folder_name_strips_leading_at` (`'@eaDir'` → `'eaDir'`)
  - `test_prefix_joined`
  - `test_options_round_trip`
- [ ] **Step 2:** Run `python -m pytest -q tests/test_lightroom_filter.py` — FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run — PASS.
- [ ] **Step 5:** Commit ("Lightroom filter and folder placement").

---

### Task 5: EXIF tags from Lightroom metadata

**Files:**
- Create: `custom_components/domosenso_photos/lightroom_exif.py`
- Test: `tests/test_lightroom_exif.py`

**Interfaces:**
- Consumes: `Photo` (Task 3); `normalize(..., add_exif=...)` (Task 2).
- Produces: `exif_adder(photo: Photo) -> Callable[[Image.Exif], None]` — sets DateTime (306) and DateTimeOriginal (36867) to `captured` as `'%Y:%m:%d %H:%M:%S'`; Make 271, Model 272, LensModel 0xA434 when set; FocalLength 0x920A, FNumber 0x829D, ExposureTime 0x829A as `IFDRational` (denominator ≤ 100000); FocalLengthIn35mmFilm 0xA405 and ISO 0x8827 as int. Nothing else.

- [ ] **Step 1: Write the failing tests:** `test_exif_round_trip_through_normalize` (a plain JPEG through `normalize(data, 200, 85, exif_adder(photo))` reads back every tag above), `test_exif_skips_missing_values` (bare meta → only the two dates), `test_exif_writes_no_gps`.
- [ ] **Step 2:** Run — FAIL. **Step 3:** Implement. **Step 4:** Run — PASS.
- [ ] **Step 5:** Commit ("EXIF tags for Lightroom photos").

---

### Task 6: Shared transport and the async Lightroom client

**Files:**
- Create: `custom_components/domosenso_photos/transport.py`
- Modify: `custom_components/domosenso_photos/albums.py` (`AlbumsManager._transport` → uses `transport.py`)
- Create: `custom_components/domosenso_photos/lightroom_api.py`
- Test: `tests/test_lightroom_api.py`

**Interfaces:**
- Produces:
  - `transport.py`: `make_transport(session: aiohttp.ClientSession) -> Transport` with the body of today's `AlbumsManager._transport` (no redirects, `MAX_BODY`, `REQUEST_TIMEOUT`, `OSError` on client errors). `Transport = Callable[..., Awaitable[tuple[int, str, bytes]]]`, signature `(method, url, params=None, data=None, headers=None)`.
  - `lightroom_api.py`:
    - `class LightroomError(Exception)`; `AuthRefused(LightroomError)`; `Unreachable(LightroomError)` with `.code: str|None` (`'redirect'`, `'client_error'`, `None`); `RenditionUnavailable(LightroomError)`.
    - `class LightroomClient(transport: Transport, client_id: str, token: Callable[[], Awaitable[str]], sleep=asyncio.sleep, clock=time.monotonic, base: str = 'https://lr.adobe.io')`
      - `async catalog_id() -> str`
      - `async assets(catalog: str) -> list[dict]` (`/v2/catalogs/{c}/assets?subtype=image&limit=100`, images only)
      - `async albums(catalog: str) -> list[dict]` (collections and sets)
      - `async album_asset_ids(catalog: str, album_id: str) -> list[str]`
      - `async image(catalog: str, asset_id: str, max_edge: int) -> bytes`
    - `account_id(token: dict) -> str | None` (claim from the Task 1 record)
    - `signin_expires(token: dict) -> datetime | None` (refresh token JWT `created_at + expires_in`, ms → aware UTC; `None` if unreadable)

Behaviour to pin: strip a leading `while (1) {}`; follow `links.next.href` joined to `base`; at least 0.2 s between calls (`clock`/`sleep`); 429/5xx/`OSError` retried after 2, 4, 8 s (`Retry-After` seconds win), then `Unreachable`; 3xx → `Unreachable('redirect')`; 401/403 on a listing → `AuthRefused`; other 4xx on a listing → `Unreachable('client_error')`. `image()`: with `max_edge <= 2048`, GET `renditions/2048`; else GET `renditions/fullsize` once and, on 404 (or any `RenditionUnavailable` case), GET `renditions/2048`. Never a POST. On a rendition, 401/403/404, a 202, or a 2xx body not starting `b'\xff\xd8'` → `RenditionUnavailable`.

- [ ] **Step 1: Write the failing tests** with a fake transport (a dict of `(method, url) → list of responses`, recording calls and headers):
  - `test_headers_carry_api_key_and_bearer`, `test_prefix_stripped`, `test_paging_follows_next`, `test_throttle_waits_between_calls`
  - `test_retry_after_then_success`, `test_5xx_three_retries_then_unreachable`, `test_redirect_is_unreachable_redirect`
  - `test_listing_401_is_auth_refused`, `test_rendition_403_is_unavailable_not_auth`, `test_rendition_non_jpeg_is_unavailable`
  - `test_image_small_edge_uses_2048`, `test_image_large_edge_uses_existing_fullsize`, `test_image_fullsize_missing_falls_back_to_2048`
  - `test_client_refuses_non_get` (`_request('POST', ...)` and `'DELETE'`/`'PUT'`/`'PATCH'` raise `ValueError` and the fake transport records no call), `test_all_calls_in_a_full_read_are_get` (a full library read plus 3 images: every recorded method is `GET`)
  - `test_account_id_from_claim`, `test_signin_expires_from_refresh_jwt`, `test_signin_expires_unreadable_is_none`
  - Existing `tests/test_album_*` stay green after the transport move.
- [ ] **Step 2:** Run — FAIL. **Step 3:** Implement. **Step 4:** `python -m pytest -q tests` — PASS.
- [ ] **Step 5:** Commit ("Async Lightroom client; transport in its own module").

---

### Task 7: Plan and execution on owned folders

**Files:**
- Modify: `custom_components/domosenso_photos/album_layout.py` (ownership by `owner` and `key`; public `inside`, `make_dirs`, `rename_target` moved from `album_sync.py`)
- Modify: `custom_components/domosenso_photos/album_sync.py` (imports the moved helpers)
- Create: `custom_components/domosenso_photos/lightroom_sync.py`
- Test: `tests/test_lightroom_sync.py`, `tests/test_album_layout.py` (ownership cases)

**Interfaces:**
- Consumes: `Placed` (Task 4); `exif_adder` (Task 5); `normalize` (Task 2); `LightroomClient.image`, `RenditionUnavailable` (Task 6); `album_files.read_marker/write_marker/write_photo/remove_photo/free_bytes`.
- Produces:
  - `album_layout.resolve_path(photo_dir: Path, wanted: str, own_id: str, key: str | None = None) -> str` — a folder is ours iff its marker's `owner` (else `album_id`) equals `own_id` and its `key` equals `key`. Existing Synology calls unchanged.
  - `album_layout.inside(photo_dir, path) -> bool`, `make_dirs(photo_dir, path) -> None` (raises `PhotoDirMissing`), `rename_target(photo_dir, path, wanted) -> str`; `class PhotoDirMissing(Exception)` (album_sync's `_PhotoDirMissing` becomes this).
  - `lightroom_sync.py`:
    - `MASS_MIN = 20`
    - `@dataclass(frozen=True) OwnedFolder(key: str, path: str, items: dict[str, dict])` — items: asset id → `{'name': str, 'changed': str}`
    - `read_owned(photo_dir: Path, prefix: str, owner: str) -> dict[str, OwnedFolder]` — scans the direct subfolders of `photo_dir/prefix` for markers with this `owner`; keyed by `key`.
    - `@dataclass(frozen=True) LrPlan(add: list[Placed], update: list[Placed], move: list[tuple[Placed, str]], remove: list[tuple[str, str]], rename: list[tuple[str, str, str]], hold: str | None)` — `move`: (placed, old key); `remove`: (key, asset id); `rename`: (key, old path, new wanted path).
    - `plan(placed: list[Placed], owned: dict[str, OwnedFolder], listed_assets: int, confirm: bool) -> LrPlan`
    - `@dataclass RunResult(added: int = 0, updated: int = 0, moved: int = 0, removed: int = 0, skipped: list[str] = field(default_factory=list))`
    - `async execute(plan: LrPlan, photo_dir: Path, owner: str, fetch: Callable[[Placed], Awaitable[bytes | None]], run_blocking: RunBlocking, free_floor: int, tz: tzinfo, now: int) -> RunResult` — raises `PhotoDirMissing`, `DiskFull`.
    - `file_name(p: Placed) -> str` = `f'{p.photo.captured:%Y-%m-%d}_{p.photo.asset_id}.jpg'`
    - Marker written per folder: `{'owner': owner, 'kind': 'lightroom', 'key': key, 'items': {...}, 'last_sync': now}`.

Plan rules: holds count files owned in all folders (`total`). `hold = 'empty_library'` when `listed_assets == 0` and `total > 0`; `'mass_remove'` when `total >= MASS_MIN` and removals > `total / 2`; `'mass_unfile'` when `total >= MASS_MIN` and moves from an album key to a `YYYY-MM` key > `total / 2`. A hold (unless `confirm`) empties `remove` and `move`; `add`, `update` and `rename` stay. An asset in two owned folders: the one matching its placement is kept, the other copy goes to `remove` (not counted towards holds). Rename: the placed folder for a key differs from the owned folder's path → `rename`, executed first with `rename_target` (only when the old folder is owned).

Execute order: renames, then for each add/update/move (free-space check before each), `fetch` → `None` means skipped; write file via `write_photo` (mtime = `captured` in `tz`), update that folder's marker; for a move then remove the old file and update the old marker; then removals. A folder left with only its marker loses the marker and the folder. New folders: `resolve_path(photo_dir, placed.folder, owner, key)` then `make_dirs`. `photo_dir` not a directory at start → `PhotoDirMissing` before anything.

- [ ] **Step 1: Write the failing tests** (tmp_path; a fake `fetch` returning a small JPEG or `None`; `run_blocking` runs inline):
  - `test_resolve_path_owner_and_key`: same owner other key → `-2`; same owner same key → reused; Synology marker (`album_id`) still resolves as before.
  - `test_plan_add_update_move_remove` (one of each from crafted `owned`/`placed`)
  - `test_plan_mass_remove_holds`, `test_plan_mass_remove_under_20_does_not_hold`, `test_plan_confirm_releases_hold`
  - `test_plan_mass_unfile_holds` (Review Focus 5), `test_plan_empty_library_holds` (Review Focus 5)
  - `test_plan_hold_keeps_adds_and_updates`
  - `test_plan_asset_in_two_owned_folders_removes_stale_copy` (Review Focus 1)
  - `test_plan_album_rename_renames_folder`
  - `test_two_albums_same_clean_name_stable_suffix` (Review Focus 2): two runs give the same two paths.
  - `test_execute_writes_file_and_marker`, `test_execute_move_writes_new_before_removing_old` (a `write_photo` spy sees the old file still present when the new one is written), `test_execute_empty_folder_removed`, `test_execute_skipped_fetch_counts`, `test_execute_disk_floor_stops`, `test_execute_photo_dir_missing_touches_nothing` (Review Focus 4), `test_execute_never_touches_unowned_folder`.
  - `tests/test_album_sync.py` stays green after the helper move.
- [ ] **Step 2:** Run — FAIL. **Step 3:** Implement. **Step 4:** `python -m pytest -q tests` — PASS.
- [ ] **Step 5:** Commit ("Lightroom sync plan and execution on owned folders").

---

### Task 8: Lightroom entry — sign-in, manager, sync

**Files:**
- Create: `custom_components/domosenso_photos/application_credentials.py`
- Create: `custom_components/domosenso_photos/lightroom.py`
- Modify: `custom_components/domosenso_photos/__init__.py` (dispatch on `entry.data.get('kind', 'albums')`)
- Modify: `custom_components/domosenso_photos/config_flow.py` (kind menu, OAuth, folder step, reauth)
- Modify: `custom_components/domosenso_photos/manifest.json` (Global Constraints)
- Modify: `strings.json`, `translations/{en,de,pl}.json`
- Test: covered by Task 10's smoke (HA objects); HA-free helpers get unit tests where added.

**Interfaces:**
- Consumes: Tasks 3–7.
- Produces:
  - `application_credentials.async_get_authorization_server(hass) -> AuthorizationServer` (the two IMS URLs, module constants `AUTHORIZE_URL`, `TOKEN_URL` so the smoke can patch them). Scopes via the flow's `extra_authorize_data = {'scope': SCOPES}`.
  - Config flow: `PhotosConfigFlow(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN)`. `async_step_user` → menu `albums` | `lightroom`. `async_step_albums` = today's user step (unique id `albums`). `async_step_lightroom` → `async_step_pick_implementation`. `async_oauth_create_entry(data)` → unique id = `account_id(data['token'])`, abort `already_configured` for a second entry of the same account; then step `lightroom_folder` (`photo_dir` default `/config/www/ramka`, `prefix` default empty; same `photo_dir` check as today) → `async_create_entry(title='Lightroom', data={'kind': 'lightroom', 'auth_implementation': ..., 'token': ..., 'photo_dir': ..., 'prefix': ...})` with default options (`filter_to_options(Filter())`, `interval_min` 360, `max_edge` 2560, `quality` 85, `free_floor` 1_000_000_000). Reauth: `async_step_reauth` → `reauth_confirm` → OAuth; on success with the same account id `async_update_reload_and_abort`; another account → abort `wrong_account`.
  - With `REDIRECT = paste` (Task 1): instead of the OAuth2 handler, a `lightroom` step shows the authorize link (redirect `https://localhost/domosenso-photos-callback`, random `state`) and a text field for the pasted address; the code is exchanged at `TOKEN_URL`; the same entry data results, and the manager refreshes tokens itself. Everything else in this task is unchanged.
  - `lightroom.LightroomManager(hass, entry, session: OAuth2Session)`: `async_load()`, `start()`, `async_close()`, `async sync(confirm: bool = False) -> dict`, `public() -> dict`. Store `Store(hass, 1, f'domosenso_photos.lightroom.{entry.entry_id}')` holding `status`, `error`, `last_sync`, `last_success`, `counts`, `photos`, `folders`, `skipped`, `skipped_at`, `hold`, `catalog` (`read_at`, `asset_count`, `photos` as dicts, `albums`), `coverage`. Owner string `f'lightroom:{account_id}'` (from `entry.unique_id`). One sync at a time per entry (`asyncio.Lock`); timer every `interval_min`, first run 60 s after start; cancelled and awaited on unload like `AlbumsManager`.
  - Sync body: `photo_dir` missing → `error`/`photo_dir_missing`; token via `session.async_ensure_token_valid()`; refresh refused or `AuthRefused` → `entry.async_start_reauth(hass)` and status `error`/`auth_failed`; read the whole library (catalog, albums, memberships, assets) → cache and coverage saved → `place` → `read_owned` → `plan` → `execute` with `fetch` = `client.image` + `normalize(..., exif_adder(photo))` in the executor, `RenditionUnavailable`/bad image → `None`. Skipped assets rest for 24 h unless their `changed` differs. Statuses as the spec lists. `entry.runtime_data = manager`.
  - Removing the entry deletes its store (`async_remove_entry`); folders stay.

- [ ] **Step 1:** Implement `application_credentials.py`, the flow, `__init__.py` dispatch (albums entries untouched), the manager and its sync body.
- [ ] **Step 2:** Strings: `config.step.user` becomes the menu (`menu_options`: `albums` "Synology Photos albums", `lightroom` "Adobe Lightroom library"); the old `user` texts move to `config.step.albums`; new `lightroom_folder`, `reauth_confirm`, aborts `wrong_account`, `already_configured`; `application_credentials.description` with the redirect to register. Same keys in en/de/pl.
- [ ] **Step 3:** Run `python -m pytest -q tests` — PASS (HA-free tests unaffected). Run the existing `albums_ha_smoke.py` per README — `albums smoke ok` (albums entries still work after the menu change; update its flow calls to choose `albums` from the menu).
- [ ] **Step 4:** Commit ("Lightroom entry: sign-in, manager and sync").

---

### Task 9: Lightroom options, preview, sensor, issues, actions, diagnostics

**Files:**
- Modify: `config_flow.py` (Lightroom options flow), `lightroom.py` (preview, issues), `sensor.py`, `services.yaml`, strings ×4
- Create: `custom_components/domosenso_photos/diagnostics.py`
- Modify: `lightroom_sync.py` — add `preview(placed: list[Placed], owned: dict[str, OwnedFolder]) -> dict`
- Test: `tests/test_lightroom_sync.py` (`preview`), smoke in Task 10

**Interfaces:**
- Produces:
  - `preview(...)` → `{'kept': int, 'add': int, 'remove': int, 'move': int, 'hold': str|None, 'top': [{'folder': str, 'add': int, 'remove': int}] (5 most changed)}`; `LightroomManager.preview(options: dict | None = None) -> dict` adds `'cache_date'`.
  - Options flow (`async_get_options_flow` picks by kind): menu `sync_now` (`confirm`), `filter`, `settings` (`interval_min` 60–1440, `max_edge` 1024–8192, `quality` 60–95, `free_floor_gb` 0.1–1000). `filter` form: `ratings` multi-select 0–5, `flag` select, `albums_include`/`albums_exclude` multi-select from the cache (`album_label`; ids missing from the cache shown as "missing album"), `include_unfiled`, `captured_from`/`captured_to` date selectors, `captured_last_years` 1–50, `edited_only`, `equipment_include`/`equipment_exclude` text (comma-separated), and — only when the Task 1 record found them — `orientation`, `keywords_include`/`keywords_exclude`. Submit → step `filter_preview` showing the counts as description placeholders; Save → `async_create_entry` (reload, then sync); a menu option `filter` goes back. Without a cache yet the preview says so and Save still works.
  - Sensor per Lightroom entry: `LightroomSensor`, `unique_id = f'{entry.entry_id}_lightroom'`, name "DomoSenso Photos Lightroom" (HA numbers further ones), state = status, attributes from `public()` (`photos`, `folders`, `last_sync`, `last_success`, `added`, `updated`, `moved`, `removed`, `skipped_count`, `error`, `signin_expires`, `cache_date`), `_unrecorded_attributes = {'folders'}`.
  - Issues: `lightroom_{entry_id}_awaiting_confirmation` (placeholder `reason`: the hold), `lightroom_{entry_id}_path_collision` (placeholder `paths`: owned folders whose path got a `-<n>` suffix because another folder held the name), `lightroom_{entry_id}_signin_expiring` when `signin_expires` < now + 3 days (removed when it moves on).
  - Actions (admin, response; registered while at least one Lightroom entry is loaded, removed with the last; album actions keep their own lifecycle): `lightroom_sync` (`entry_id` optional, `confirm` bool) → `{'libraries': [public, ...]}`; `lightroom_preview` (`entry_id` optional) → `{'libraries': [preview, ...]}`. Unknown `entry_id` → `ServiceValidationError` key `unknown_library`.
  - `diagnostics.async_get_config_entry_diagnostics(hass, entry) -> dict`: Lightroom: `kind`, `status`, `error`, `asset_count`, `albums`, `sets`, `coverage` (field → count), never values or tokens. Albums entry: `kind`, album count, statuses.

- [ ] **Step 1: Write the failing test** `test_preview_counts_and_top_folders` (crafted placed/owned: 3 adds into A, 2 removals from B, 1 move → `{'kept': ..., 'add': 3, 'remove': 2, 'move': 1, 'hold': None}`, `top[0]['folder'] == 'A'`), `test_preview_reports_hold`.
- [ ] **Step 2:** Run — FAIL. **Step 3:** Implement `preview`, then the rest above. **Step 4:** `python -m pytest -q tests` — PASS.
- [ ] **Step 5:** Commit ("Lightroom options with preview, sensor, issues, actions and diagnostics").

---

### Task 10: Lightroom HA smoke

**Files:**
- Create: `tests/lightroom_ha_smoke.py`
- Modify: `README.md` (Development: how to run it)

Runs like `albums_ha_smoke.py` (official image 2026.9.2, `--entrypoint python`), last line `lightroom smoke ok`, else `lightroom smoke failed: <reason>`, exit 1. A fake IMS + Lightroom on `127.0.0.1` (aiohttp web app, invented data: 30 assets in 3 albums, a set, 2 unfiled, varied ratings/flags/dates/cameras). Patch `application_credentials.AUTHORIZE_URL/TOKEN_URL` and `lightroom_api` base to the fake. The fake Lightroom answers `405` to anything but `GET` and the smoke ends with `check(fake.non_get == 0, ...)`. Drive OAuth by configuring the flow with `{'state': <state from the authorize URL>, 'code': 'c1'}` after the external step (paste variant: submit the pasted address).

- [ ] **Step 1:** Write the smoke covering, each as a `check(...)`: menu → lightroom → OAuth → folder step → entry created; second entry same account aborts; first sync writes exactly the default-filter photos (3★+, not rejected) in album/month folders with markers and EXIF and no GPS; `edited_only` + exclude-album filter through the options flow, preview counts shown, Save → files follow; filter removing > half → `awaiting_confirmation` + repair issue, `lightroom_sync confirm: true` releases it; an edit in the fake (changed `userUpdated`) re-downloads that one file; a rendition 404 → `skipped_count` 1, status `ok`; token endpoint answering 400 → reauth flow in progress; refresh JWT expiring in 2 days → `signin_expiring` issue; `lightroom_preview` response; diagnostics has coverage and no token; unload cancels; remove entry deletes the store and keeps the folders; re-adding the same account reuses them (no `-2`, no downloads); albums entry alongside still syncs (both kinds loaded together).
- [ ] **Step 2:** Run it per README — `lightroom smoke ok`. Run `albums_ha_smoke.py` — `albums smoke ok`.
- [ ] **Step 3:** Commit ("Lightroom HA smoke against a fake Adobe").

---

### Task 11: Documentation

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `hacs.json` (only if a field must change; none expected)

- [ ] **Step 1:** README: title line and features cover both sources; "Lightroom library" section — Adobe Developer Console project (OAuth Web App, Lightroom Services, redirect to register), Application Credentials, setup steps, the filter table and its rules, preview, folders and files (`<prefix>/<album>`, month folders, file names, rendition choice and `max_edge`), safety holds, statuses, sensor, actions, sign-in renewal and the `signin_expiring` issue, coexistence with another tool writing the same folder, limits (partner API, 2048 px if no fullsize), uninstall; "Not affiliated with or endorsed by Adobe Inc." next to the Synology line.
- [ ] **Step 2:** CHANGELOG "Unreleased": the Lightroom source, the filter and preview, and the ICC fix from Task 2.
- [ ] **Step 3:** Run `python -m pytest -q tests` — PASS (no code change).
- [ ] **Step 4:** Commit ("Document the Lightroom source").

---

### Task 12: Prototype on the author's Home Assistant, then the pull request

Every write to the author's Home Assistant needs their go-ahead typed in the session.

- [ ] **Step 1:** With the go-ahead: install this branch's `custom_components/domosenso_photos` on their Home Assistant, restart, add Application Credentials, add a Lightroom entry with a photo folder **that no other tool writes and no slideshow shows**.
- [ ] **Step 2:** First sync with the default filter; compare the set of asset ids per album folder with what their existing Lightroom tool kept (read-only on that tool's files); differences explained or fixed. Show the author the preview and a filter change.
- [ ] **Step 3:** Author confirms on the device. Leave the entry in place or remove it, as the author says.
- [ ] **Step 4:** With the go-ahead: push `lightroom-source`, open the pull request into `main` with a summary per task and screenshots of the new flows and options (setup menu, folder step, filter, preview, repair issue).
