# DomoSenso Photos for Home Assistant

[![HACS custom repository](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Validate](https://github.com/zerostorypoints/domosenso-photos/actions/workflows/validate.yml/badge.svg)](https://github.com/zerostorypoints/domosenso-photos/actions/workflows/validate.yml)
[![Tests](https://github.com/zerostorypoints/domosenso-photos/actions/workflows/tests.yml/badge.svg)](https://github.com/zerostorypoints/domosenso-photos/actions/workflows/tests.yml)

Keep a photo folder in Home Assistant in step with shared Synology Photos albums or with an Adobe
Lightroom library. A Synology album is added by its share link, copied into its own folder as
screen-sized JPEG files and updated on a timer; the share link is the only access, no Synology
account or token is stored. A Lightroom library is read through your own Adobe sign-in: the photos
that pass a filter you set are copied into a folder tree that mirrors Lightroom's albums and sets.

The integration fills a folder. Showing the photos (which one is drawn next, rotation, captions)
is the job of a slideshow that reads that folder, for example the Ramka component of
[DomoSenso](https://github.com/zerostorypoints/domosenso).

**Version 0.1.2. Requires Home Assistant 2026.9.2 or newer.**

---

## Features

- **Two sources, chosen when you add the integration:** Synology Photos albums, and an Adobe
  Lightroom library (cloud). Each is its own entry; see [Lightroom library](#lightroom-library).
- **Synology: share links only.** Paste a Synology Photos share link, with its password if it has one.
- **One folder per album**, `<photo folder>/<YYYY>/<MM>/<album name>` by default, or a folder
  you choose. A remote rename of the album renames the folder.
- **Screen-sized JPEG.** Every photo is converted on import to a JPEG of at most 2560 pixels on
  the longest edge (configurable). Originals are not kept.
- **Location removed.** GPS data is always stripped; date, camera and lens data stay.
- **HEIC and Live Photos.** HEIC/HEIF photos are converted with `pillow-heif`; Live Photos are
  synced as their still image. Videos are ignored.
- **Safety holds.** A sync that would delete more than half of an album, or empty it, deletes
  nothing and asks for confirmation through a repair issue.
- **Your own files stay.** Only folders the integration created are ever written, renamed or
  deleted.
- **Host map** for a NAS whose public name does not answer from inside your network.
- **Actions** to add, remove and sync albums from automations (administrators only), and a sensor
  with the state of every album.
- **Lightroom, selected by a filter:** stars, flag, albums, dates, edited photos, camera or lens,
  with a preview before a change is saved. Edited photos are fetched as edited in Lightroom.
- **Lightroom is read-only:** nothing is ever changed or deleted in Adobe's cloud.
- English, German and Polish translations.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=zerostorypoints&repository=domosenso-photos&category=integration)

Or by hand:

1. HACS → ⋮ → **Custom repositories**.
2. Repository `https://github.com/zerostorypoints/domosenso-photos`, type **Integration**.
3. Install **DomoSenso Photos** and restart Home Assistant.

### Manual

Copy `custom_components/domosenso_photos` into the `custom_components` folder of your Home
Assistant configuration and restart.

## Setup

### Share link requirements

Create the share link in Synology Photos, with a password if you want one (a link without a
password works too). It also needs the **download** permission. Without it the sync uses the
share's `xl` thumbnails: the photos are smaller, carry no camera or lens data, and the album
reports mode `thumbnails` instead of `originals`.

The share-link API of Synology Photos is undocumented and may change with DSM updates; it was
verified on DSM in November 2026.

### Add the integration

Go to **Settings → Devices & services → Add integration → DomoSenso Photos** and choose
**Synology Photos albums** (for Lightroom see [Lightroom library](#lightroom-library)). There is
one Synology entry per Home Assistant. The only field is the photo folder, an existing absolute path (default
`/config/www/ramka`). Everything under `/config/www` is served without login, so add only albums
you are fine to show there.

### Add albums

Use **Configure** on the entry and choose **Add album**: share link, optional password and an
optional folder inside the photo folder (for example `2026/11/Holiday`) instead of the default
layout. The first sync runs right away. If it ends with any status other than `ok` or
`awaiting_confirmation`, the form reports it (wrong password, unreachable host, or a general share
error) and nothing is kept.

**Remove album** deletes the photos the album synced, any leftover temporary files and the
album's sync marker, and then the folder itself only if it is empty. Files you added to the folder
by hand stay, and so does the folder. The shared album is not changed. While the album is
syncing, the form answers that the album is busy; try again afterwards.

**Sync now** syncs one album or all of them in the background; the sensor shows the result.

## What a synced album is

- A folder `<photo_dir>/<YYYY>/<MM>/<album name>`. Year and month come from the album's earliest
  photo at the first sync and then stay fixed. Folder names are cleaned of path separators and
  control characters, lose leading spaces and dots and are cut to 80 characters and 200 bytes. If
  another folder already holds the name, the album gets a `-2` suffix (`-3`, ...) and Home
  Assistant shows a repair issue. When the album is renamed in Synology Photos, only the last
  part of the folder path is renamed.
- Every photo is a JPEG of at most `max_edge` pixels on the longest edge (default 2560, quality
  85).
- EXIF is kept, with these exceptions: the orientation is applied to the pixels and reset, the GPS
  location is always removed (files under `/config/www` are served without login), and an EXIF
  block that does not fit a JPEG is reduced, first by dropping MakerNote and the embedded
  thumbnail, then to date, camera, lens and exposure tags.
- A hidden `.sync.json` marker in each synced folder names the album and lists the files that
  belong to it. It holds no link and no password. Folders without a marker are never written,
  renamed or deleted.
- A synced folder is managed in Synology Photos: files you add or change there by hand are not
  tracked, a photo removed from the share is removed from the folder, and a photo you delete by
  hand is downloaded again at the next sync.

## Options

**Configure → Settings** holds:

| Option | Default | Range |
| --- | --- | --- |
| Sync interval | 60 min | 15-1440 |
| Longest edge | 2560 px | 1024-8192 |
| JPEG quality | 85 | 60-95 |
| Keep free on disk | 1 GB | 0.1-1000 GB |
| Host map | empty | |

Saving the settings reloads the entry.

### Sync schedule

An album is synced every `interval_min` minutes, about 60 seconds after Home Assistant starts,
and on demand (**Sync now** or the `album_sync` action). The sync does not notify the slideshow;
the slideshow sees new files when it next reads the folder.

Each sync asks for the album's version first. Synology raises it when a photo is added or removed
(and when the link's settings change). While the version and the album's name are unchanged and
every synced file is still in its folder, the sync skips the photo listing, except for a full
listing at least once every 24 hours. A sync waiting for a deletion to be confirmed always lists
the album.

Albums sync one at a time. A sync that is due while another album syncs waits for it, and the
waiting album already shows `syncing`.

### Keep free on disk

A sync stops with status `disk_full` when the free space on the photo folder's disk would drop
below the floor; the check runs before each file. Files already written stay.

### Host map

If the NAS's public name does not answer from inside your network (no NAT loopback), map it to
its LAN address. One line per mapping, each side an origin only (scheme, host, optional port; no
path):

```text
https://photos.example.com -> http://192.168.1.20:5000
```

The share link itself stays unchanged; only the address Home Assistant connects to differs.

Redirects are not followed: a `3xx` answer ends the sync with `unreachable` / `redirect`. Map to
the address that answers directly, for example the `https://` address with its port when the
`http://` one redirects there. With an `https://` target the certificate must be valid for that
name.

## Actions

Three actions, all for administrators only, return a response:

| Action | Fields | Response |
| --- | --- | --- |
| `domosenso_photos.album_add` | `url`, `path` (optional); no password | `album` |
| `domosenso_photos.album_remove` | `album_id`; deletes the synced photos and the marker, the folder only if empty | `removed` |
| `domosenso_photos.album_sync` | `album_id` (empty syncs all), `confirm_deletions` | `albums` |

The `album_id` is the `id` from the sensor attribute.

`album_add` takes no password: Home Assistant records the data of every action call in its
`call_service` events, so a password would end up in the recorder database. Add
password-protected links in the options form; code running in the same Home Assistant can call
the albums manager directly and pass a password there. A link that needs a password comes back
from `album_add` with status `auth_failed`. The action keeps the album whatever its first sync
returns (for example `auth_failed` or `unreachable`); fix the cause and sync again, or remove it.

`album_remove` on a syncing album reports `removed: false`; `album_sync` on a syncing album
returns it with status `syncing`.

Example: sync every album at night.

```yaml
automation:
  - alias: Sync photo albums at night
    triggers:
      - trigger: time
        at: "03:00:00"
    actions:
      - action: domosenso_photos.album_sync
```

## For other integrations

A paired DomoSenso Frame device manages albums through a small in-process facade. It is an
internal contract between integrations, not a Home Assistant service:

```python
api = hass.data['domosenso_photos_albums'].frame_api   # present while the entry is loaded
```

`api.version` is `1`. A caller checks it and treats a higher number as unsupported; a breaking
change raises it.

| Method | Result |
| --- | --- |
| `api.albums()` | The items of the sensor's `albums` attribute (no link, no password). |
| `await api.check(url, password)` | The album name. Bounded to 10 s; nothing is saved. |
| `await api.add(url, password)` | `{'id': ..., 'name': None}`. The album is saved and its first sync runs in the background. |
| `await api.remove(album_id)` | `'removed'`, or `'busy'` while its sync runs (the album stays). |
| `await api.sync(album_id)` | Returns at once; the sync runs in the background. |

Failures raise `AlbumError` with `.code` one of `invalid_link`, `already_added`, `wrong_password`,
`unreachable` (`check`, `add`), `unknown_album` (`remove`, `sync`) or `photos_unavailable` (all
four, while the entry unloads; nothing is changed then). The password is never
part of a URL, an error text or a returned value; a caller must not log it either.

## Sensor

`sensor.domosenso_photos_albums` shows the number of albums. Its `albums` attribute lists, per
album: `id`, `name`, `path`, `photos`, `last_sync`, `status`, `error`, `mode`, `heic` and
`skipped_count`. It never contains links or passwords, and the recorder does not store it.

| Status | Meaning |
| --- | --- |
| `ok` | Synced. |
| `syncing` | A sync is running. |
| `awaiting_confirmation` | A deletion hold, see below. |
| `auth_failed` | The link password is wrong or missing. |
| `unreachable` | The share host does not answer; check the link, the network or the host map. `error` is `redirect` for a redirect and `too_large` for a response over 200 MB. |
| `disk_full` | The free-space floor was reached. |
| `error` | See `error`, for example `incomplete_listing`, `unexpected_response`, `write_failed`, `invalid_path`, `photo_dir_missing`, `interrupted`. |

An original that cannot be decoded is replaced by the share's `xl` thumbnail. If that cannot be
decoded either, the photo is skipped and counted in `skipped_count`. Skipped photos are tried
again at most once every 24 hours while the album's version is unchanged; a changed album version
retries them at the next sync.

## Safety holds

Nothing is deleted unless the login, the album request and the complete photo listing all
succeed. If the listing is incomplete, the sync fails with `incomplete_listing` and keeps every
file. If more than half of an album's photos would disappear, or an album that had photos comes
back empty, nothing is deleted: the status becomes `awaiting_confirmation` and a repair issue
appears. If the change is intended, confirm it with `album_sync` and `confirm_deletions: true` for
that album, or with **Sync now** and **Confirm held deletions** in the options. A confirmation
applies only to albums that already wait for it when the call is made; an album that enters a
hold during the same run keeps it.

If the photo folder itself is missing (for example unmounted storage), every sync ends with
`error` / `photo_dir_missing` and changes nothing; the sync never creates the photo folder, only
folders below it.

## Lightroom library

Reads an Adobe Lightroom (cloud) library, selects photos with a filter and copies them into a folder
tree that mirrors Lightroom's albums and sets. It is a second kind of entry, next to the Synology one; both can run at
the same time. Lightroom Classic (desktop catalogs) is not supported.

The integration only reads. Every request to Lightroom is a `GET`; the only `POST` is the token
exchange at Adobe's sign-in. Nothing is changed, generated or deleted in Adobe's cloud: no photo,
album, rating, flag or rendition. Adobe's Lightroom permission is not read-only, so the client
refuses any other request method before sending it.

### Adobe project

You bring your own Adobe client; the integration ships none. In the
[Adobe Developer Console](https://developer.adobe.com/console) create a project with the
**Lightroom Services** API as an **OAuth Web App**, and register
`https://my.home-assistant.io/redirect/oauth` as its redirect URI.

- Use a separate project for each tool that signs in to your Lightroom (this integration, any
  other script or container), so that each has its own client ID and secret.
- Adobe may not offer Lightroom Services to new projects on some accounts (inference from reports
  in Adobe's community forum). If it is not offered, an existing project that has it can be used:
  add Home Assistant's redirect next to the redirects it already has.
- [My Home Assistant](https://my.home-assistant.io) must point to the same address you open Home
  Assistant with. Otherwise the sign-in ends with "Invalid state".

### Add the library

1. **Settings → Devices & services → Add integration → DomoSenso Photos → Adobe Lightroom
   library.** If no Adobe credentials exist yet, Home Assistant asks for them
   (Application Credentials): the project's client ID and client secret.
2. Sign in to Adobe in the window that opens and allow access.
3. Choose the photo folder (an existing absolute path, default `/config/www/ramka`) and an optional
   subfolder, for example `lightroom`. Both are fixed afterwards: changing them would move every
   photo. Everything under `/config/www` is served without login.

There is one entry per Adobe account; adding the same account again is refused. The first sync
starts a minute after setup and reads the whole library before it writes anything.

### Filter

A photo is synced when it meets every condition that is set. An empty field sets no condition.
Edit it under **Configure → Filter**.

| Condition | Values | Default |
| --- | --- | --- |
| Ratings | any of 0 (unrated) to 5; at least one | 3, 4, 5 |
| Flag | any, not rejected, picks only | not rejected |
| Only these albums | albums or sets of albums; a set means every album below it | empty (all) |
| Never these albums | the same; wins over the line above | empty |
| Photos in no album | on or off | on |
| Taken from, until | dates, both inclusive | empty |
| Taken in the last years | 1 to 50; replaces the two dates when set | empty |
| Edited photos only | on: only photos changed from Lightroom's defaults | off |
| Camera or lens contains / does not contain | comma-separated text, matched anywhere in make, model or lens, ignoring case | empty |

Album rules:

- With "Only these albums" set, a photo passes only through an included album that is not
  excluded, and goes into one of those albums. A photo in several albums goes into the first by
  name (ignoring case, then by id).
- A photo whose every album is excluded is not synced; it does not fall back to a month folder.
- A photo in no album passes only if "Photos in no album" is on.
- An album that no longer exists stays in the list as "missing album" and is ignored.

### Preview

**Next** on the filter form does not save. It shows, from the library as last read (with its
date), how many photos the next sync would keep, add, remove and move, whether a safety hold would
apply, and the five folders with the most change. **Save** applies the filter (the entry reloads
and syncs); **Back to the filter** returns to the form. The action `domosenso_photos.lightroom_preview`
returns the same counts for the saved filter and makes no request to Adobe. Before the first
sync there is no library to count; Save still applies the filter.

### Folders and files

- Albums: `<subfolder>/<set>/…/<album name>`, the album's path in Lightroom's album tree
  (`2023/Iceland/Day 1`). Only album folders hold photos and the record `.sync.json`; set folders
  are plain folders. Photos in no album: `<subfolder>/<YYYY-MM>` of their capture date. Without a
  subfolder the folders sit directly in the photo folder.
- An album renamed or moved in Lightroom, or a set renamed or moved, moves the album folders on the
  next sync, with their photos; nothing is downloaded again. A set folder that such a move (or a
  removal) leaves empty is deleted; one that still holds anything (a file of yours, a NAS's
  `@eaDir`) stays, and so does any other empty folder. Folders from the earlier flat layout (one
  folder per album) move into the tree the same way on the first sync after an update.
- An album with the same name as a set beside it gets `-2`, so an album folder never holds another
  album's folder.
- Album names are cleaned like Synology ones, and a leading `@` is dropped (slideshows often skip
  folders starting with `.` or `@`). If two names collide or another folder has the name, the
  later one gets `-2` and a repair issue appears; its path is in the sensor's `path_collisions`.
- File name: `<YYYY-MM-DD>_<asset id>.jpg`, with the capture date as modification time.
- Every photo passes the same conversion as a Synology photo: longest edge `max_edge`, JPEG
  quality, orientation applied, GPS never written, colour profile kept. Date, camera, lens and
  exposure are added from the photo's Lightroom metadata, because Adobe's rendition has none.
- Lightroom's edits are applied (Adobe renders them). A photo edited in Lightroom is downloaded
  again, a photo moved to another album moves, and one that no longer passes the filter or leaves
  the library is removed.
- A rendition that cannot be fetched or decoded is skipped, counted in `skipped_count` and tried
  again after a day. Only the date, camera, lens and exposure tags above are written; anything else
  a rendition carries (serial numbers, maker notes, location) is dropped.

### Resolution

Photos are at most 2048 px on the longest edge, unless Lightroom already holds a full-size
rendition and `max_edge` is above 2048. The integration never asks Adobe to generate one (that would
add a file to your cloud), so a photo without it stays at 2048 px; it is never enlarged.

### Which folders it touches

Only folders that carry its own `.sync.json` marker, and only the files the marker lists. Other
files in them stay.

- **Copies.** If you copy a synced folder, so that more than one folder carries the marker of the
  same album, the integration cannot tell which is its own. It then touches none of that album's
  folders and shows the repair issue "copied folders are left alone". The sensor attribute
  `ambiguous_folders` lists them; delete the copy, or the `.sync.json` inside it, and the next
  sync continues.
- **Moved folders.** A synced folder you move or rename on purpose is moved back to its place at
  the next sync.
- **Another tool writing the same folder.** The integration cannot know about it, and would put
  its own folders next to the other tool's with a `-2` suffix. Use a separate photo folder (or a
  subfolder) until the other tool is stopped and its files are removed. Files written by another
  tool are not taken over.
- **Synology albums in the same photo folder.** Give Lightroom a subfolder (set at setup), so its
  album folders and the Synology ones never meet. A Lightroom folder that holds any subfolder is
  never renamed or removed; its own photos still sync.
- Repair issues name no folders, because Home Assistant includes repair issues in diagnostics
  downloads; they point to the sensor attributes instead.

### Safety holds

A sync that would remove or move most of what it manages waits instead. Nothing is removed or moved
(new photos are still added), the status is `awaiting_confirmation` and a repair issue appears when:

| Hold | When |
| --- | --- |
| `mass_remove` | at least 20 files are managed and more than half would be removed |
| `mass_unfile` | at least 20 files are managed and more than half would move to month folders (for example an album listing that came back empty) |
| `empty_library` | Adobe lists no photos although files are managed |

If the change is intended, use **Configure → Sync now → Confirm held changes**, or the action
`domosenso_photos.lightroom_sync` with `confirm: true`. A confirmation applies to a hold that exists
when it is given, and only to that hold: if the sync then finds another reason to hold (for example
the library suddenly lists no photos), it waits again. A held sync renames no folder either. A filter change that removes more than half asks for it after Save; the preview
shows it first.

### Settings, schedule and status

**Configure → Settings**:

| Option | Default | Range |
| --- | --- | --- |
| Sync interval | 360 min | 60-1440 |
| Longest edge | 2560 px | 1024-8192 |
| JPEG quality | 85 | 60-95 |
| Keep free on disk | 1 GB | 0.1-1000 GB |

Settings also holds the **Photo folder** and the **Subfolder** chosen when the library was added.
Changing either asks for a confirmation first; a running sync is stopped. After it the entry
reloads and syncs into the new folder: every photo is downloaded again, unless that folder
already holds this library's photos (switching back to an earlier folder downloads only what
changed). The photos in the old folder are left where they are; the integration does not delete
them.

The library is synced about 60 seconds after Home Assistant starts, every interval, after a
filter is saved and on demand. Adobe is asked for at most one request every 0.2 s; a `429` or `5xx`
answer, or a network error, is retried with back-off (honouring `Retry-After`, at most 300 s). If
reading the library still fails, the sync ends with `unreachable` and changes nothing. A photo
whose rendition still fails is skipped like any rendition that cannot be fetched; when 5
renditions in a row fail that way, Adobe is taken to be down: the sync stops with `unreachable`,
keeping the photos it wrote so far, and the next sync continues.

The sensor `sensor.domosenso_photos_lightroom` (a second library gets a number) has the sync
status as its state: `ok`, `syncing`, `awaiting_confirmation`, `unreachable`, `disk_full` or
`error` (see `error`). Its attributes: `photos`, `folders` (not recorded), `last_sync`,
`last_success`, `added`, `updated`, `moved`, `removed`, `skipped_count`, `error`,
`signin_expires`, `cache_date`, `path_collisions` and `ambiguous_folders` (the last two not
recorded). While a sync runs, `phase`, `done`, `total` and `progress` (in %) say how far it is:
- `reading_library`: `done` counts the listing pages read so far (a library of 40,000 photos takes
  several hundred), `total` is the count of the last read (empty on the first one), and `progress`
  stays at 99 at most until the reading ends;
- `downloading`, then `removing`: `total` is the photos to write and remove, `done` those handled
  so far (skipped ones included).

Outside a sync `phase`, `done` and `total` are empty and `progress` is 100 after a sync that
finished, else empty. They are updated at most every 5 seconds.
The actions `domosenso_photos.lightroom_sync` (`entry_id`, `confirm`) and
`domosenso_photos.lightroom_preview` (`entry_id`) are for administrators and return a response.
**Configure → Sync now** also syncs in the background.

### Sign-in renewal

Adobe's refresh token lives about 14 days. Home Assistant stores the renewed one after each
refresh, and the token has been seen to renew itself on refresh (observed, not documented by
Adobe). The syncs therefore keep the sign-in alive. When it is less than 3 days from its end, the
repair issue "Adobe sign-in ends soon" appears and goes when a refresh moves it on. If Adobe
refuses the sign-in, Home Assistant asks you to sign in again with the same account (reauthentication);
nothing is deleted meanwhile.

### Lightroom limits

- Partner API of Adobe: it is not a public, stable interface and may change.
- Photos are at most 2048 px unless Lightroom already has a full-size rendition (see above).
- Videos, people (faces) and Lightroom Classic are not supported.
- The whole library is read on every sync.
- Diagnostics (download from the entry) carry counts of the metadata Adobe returned, never values,
  tokens or names.

### Removing a library

Removing the entry deletes its sign-in and its cached library; the folders stay. Adding the same
Adobe account again takes them back, so no second copy is downloaded next to them.

## HEIC photos

HEIC/HEIF photos are converted with `pillow-heif` 1.8.1. It is deliberately not a manifest
requirement: when the entry is set up, it asks Home Assistant to install the package in the
background. Setup neither waits for the install nor fails with it; a failed install (for example
without network, or no wheel for the platform) logs a warning. Until the package can be imported,
HEIC items use the share's `xl` thumbnail and the album reports `heic: thumbnails`; afterwards it
reports `heic: converted`.

In the Home Assistant Container image the package is installed into the image, so after each
image update the next setup installs it again and needs network access for that.

## Limits

- Synology Photos share links only: an `http` or `https` link whose path contains
  `/mo/sharing/<passphrase>`, for example `https://photos.example.com/mo/sharing/AbCdEf123`.
  Links with a user name or password in the URL are refused.
- One Synology entry, and so one Synology photo folder, per Home Assistant.
- Transport timeouts are 30 s to connect, 60 s without data and 15 minutes per request; responses
  over 200 MB are refused. Requests identify as `domosenso-photos/0.1.2`.
- The share-link API is undocumented; a DSM update can change it.

## Uninstall

Remove an entry under **Settings → Devices & services → DomoSenso Photos**. This deletes the
stored links and passwords; every synced folder stays as a plain folder and a slideshow keeps
showing it. The hidden `.sync.json` markers can be deleted to tidy up, which changes nothing else:
a new entry gives every album a fresh id, so it never adopts an old marker. Adding the same link
again creates a second copy next to the old folder (with a `-2` suffix and a repair issue) rather
than reusing it.

## Troubleshooting

- **Wrong password in the form, or `auth_failed`.** The link password is wrong, missing, or was
  changed in Synology Photos. Remove the album and add it again with the current password.
- **`unreachable`.** Check the link in a browser on the same network as Home Assistant. If the
  public name does not answer from inside, add a host map line. `error: redirect` means the
  address redirects; map to the address that answers directly.
- **Mode `thumbnails`.** The share link has no download permission. Enable it in Synology Photos.
- **`heic: thumbnails`.** `pillow-heif` is not installed yet or could not be installed. Check the
  log for the warning, make sure Home Assistant has network access and reload the entry.
- **`awaiting_confirmation`.** See [Safety holds](#safety-holds).
- **`photo_dir_missing`.** The photo folder does not exist (for example a network share that is
  not mounted). Mount it; the next sync continues.
- **A repair issue about another folder.** The wanted folder already belonged to something else.
  Rename or move the other folder, or remove the album and add it with its own folder.

## Privacy and security

- Links and passwords are stored in Home Assistant's `.storage/domosenso_photos.albums`, never
  under the photo folder; removing the entry deletes that store. Keep Home Assistant backups
  private.
- The photo folder holds only photos and sync markers; the markers carry no link and no password.
- Files under `/config/www` are served by Home Assistant without login. GPS location is always
  removed for that reason, but the photos themselves are readable by anyone who can reach Home
  Assistant and knows the path.
- Only administrators can call the actions.
- The Synology source talks only to the share host (or its host-map address); the Lightroom source
  only to Adobe's sign-in and Lightroom hosts.
- The Adobe client secret and the sign-in are stored by Home Assistant (Application Credentials
  and the entry), never in the photo folder.

## Development

```bash
python -m pip install -r requirements-test.txt
python -m pytest -q tests
```

`tests/albums_ha_smoke.py` boots Home Assistant, creates the entry through the config flow and
syncs against a fake Synology Photos share server on `127.0.0.1` (host map
`http://photos.invalid -> http://127.0.0.1:<port>`). It covers the config and options flows, the
actions with their responses and admin check, the sensor, repair issues, the `pillow-heif`
install by the entry, HEIC conversion, safety holds and their confirmation, error states, startup
sync, unload and entry removal. All data is fictional.

Run it from the repository root with Docker. It needs network access because the entry has Home
Assistant install `pillow-heif`; the share server itself is local.

```bash
HA_SMOKE_CONFIG="$(mktemp -d)"
docker run --rm \
  --mount "type=bind,src=$HA_SMOKE_CONFIG,dst=/config" \
  --mount "type=bind,src=$PWD/custom_components,dst=/config/custom_components,readonly" \
  --mount "type=bind,src=$PWD/tests/albums_ha_smoke.py,dst=/albums_ha_smoke.py,readonly" \
  --entrypoint python ghcr.io/home-assistant/home-assistant:2026.9.2 /albums_ha_smoke.py
rm -rf "$HA_SMOKE_CONFIG"
```

The last line is `albums smoke ok` with exit code 0; a failed check prints
`albums smoke failed: <reason>` and exits 1. Set `ALBUMS_SMOKE_DEBUG=1` (`-e` on `docker run`) to
keep Home Assistant's log output.

`-e ALBUMS_SMOKE_OFFLINE=1` on a network without internet access checks that the entry still
loads when the install fails and that HEIC then uses the thumbnail. Use an internal Docker network
(`docker network create --internal albums-smoke-offline`, then `--network albums-smoke-offline`);
with `--network none` the container has no IPv4 address and Home Assistant cannot create any HTTP
client session, which is unrelated to `pillow-heif`. The failing install takes about three
minutes before the test continues.

`tests/lightroom_ha_smoke.py` does the same for the Lightroom source, against a fake Adobe IMS and
Lightroom on `127.0.0.1`. The library is invented: 30 assets in three albums, two of them inside a
set, plus two unfiled assets. The smoke points `application_credentials.AUTHORIZE_URL` /
`TOKEN_URL` and `lightroom_api.API_BASE` at the fake and drives the OAuth redirect the way Home
Assistant's callback view does. It covers:

- the config flow and a second entry for the same account;
- the first sync (folders, markers, EXIF, no GPS, serial numbers or maker notes);
- the filter options with their preview, a held mass removal and its confirmation;
- an edit re-downloading one photo, and a missing rendition skipped;
- ambiguous and colliding folders, and what their issues leave out of the diagnostics download;
- a refused refresh starting reauth, and the sign-in-expiring issue;
- two accounts at once, with the actions' lifecycle;
- unload during a sync, removing and re-adding the entry;
- an albums entry syncing alongside.

The fake Lightroom answers 405 to anything but `GET`; the smoke fails if it ever got such a
request. Nothing contacts Adobe.

```bash
HA_SMOKE_CONFIG="$(mktemp -d)"
docker run --rm \
  --mount "type=bind,src=$HA_SMOKE_CONFIG,dst=/config" \
  --mount "type=bind,src=$PWD/custom_components,dst=/config/custom_components,readonly" \
  --mount "type=bind,src=$PWD/tests/lightroom_ha_smoke.py,dst=/lightroom_ha_smoke.py,readonly" \
  --entrypoint python ghcr.io/home-assistant/home-assistant:2026.9.2 /lightroom_ha_smoke.py
rm -rf "$HA_SMOKE_CONFIG"
```

The last line is `lightroom smoke ok` with exit code 0; a failed check prints
`lightroom smoke failed: <reason>` and exits 1. `LIGHTROOM_SMOKE_DEBUG=1` keeps the log output.

## Credits

Made by [Zero Story Points](https://zerostorypoints.com).

Not affiliated with or endorsed by Synology Inc. Synology is a trademark of its owner.
Not affiliated with or endorsed by Adobe Inc. Adobe and Lightroom are trademarks of their owner.

## License

[Apache-2.0](LICENSE)
