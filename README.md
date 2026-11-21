# DomoSenso Photos for Home Assistant

[![HACS custom repository](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Validate](https://github.com/zerostorypoints/domosenso-photos/actions/workflows/validate.yml/badge.svg)](https://github.com/zerostorypoints/domosenso-photos/actions/workflows/validate.yml)
[![Tests](https://github.com/zerostorypoints/domosenso-photos/actions/workflows/tests.yml/badge.svg)](https://github.com/zerostorypoints/domosenso-photos/actions/workflows/tests.yml)

Keep a photo folder in Home Assistant in step with shared Synology Photos albums. Each album is
added by its share link, copied into its own folder as screen-sized JPEG files and updated on a
timer. The share link is the only access: no Synology account or token is stored.

The integration fills a folder. Showing the photos (which one is drawn next, rotation, captions)
is the job of a slideshow that reads that folder, for example the Ramka component of
[DomoSenso](https://github.com/zerostorypoints/domosenso).

**Version 0.1.0 is a pending release. Requires Home Assistant 2026.9.2 or newer.**

---

## Features

- **Share links only.** Paste a Synology Photos share link, with its password if it has one.
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

Go to **Settings → Devices & services → Add integration → DomoSenso Photos**. There is one entry
per Home Assistant. The only field is the photo folder, an existing absolute path (default
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
- One entry, and so one photo folder, per Home Assistant.
- Transport timeouts are 30 s to connect, 60 s without data and 15 minutes per request; responses
  over 200 MB are refused. Requests identify as `domosenso-photos/0.1.0`.
- The share-link API is undocumented; a DSM update can change it.

## Uninstall

Remove the entry under **Settings → Devices & services → DomoSenso Photos**. This deletes the
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
- The integration talks only to the share host (or its host-map address).

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

## Credits

Made by [Zero Story Points](https://zerostorypoints.com).

Not affiliated with or endorsed by Synology Inc. Synology is a trademark of its owner.

## License

[Apache-2.0](LICENSE)
