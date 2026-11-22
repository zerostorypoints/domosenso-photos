# Changes

## Unreleased

- Adobe Lightroom (cloud) as a second photo source, next to Synology albums: sign in through
  Application Credentials, one folder per Lightroom album and one per month for photos in no album,
  edits as in Lightroom, read-only towards Adobe. Adds the `lightroom_sync` and `lightroom_preview`
  actions, a Lightroom sensor, safety holds and repair issues (including `signin_expiring`).
- Filter for the Lightroom photos (ratings, flag, albums, dates, edited, camera or lens) with a
  preview of what a change would add and remove before it is saved.
- Lightroom: progress while syncing, also while the library is read (Lightroom sensor attributes
  `phase`, `done`, `total` and `progress` in %).
- Lightroom: the photo folder and subfolder can be changed in Settings, also during a sync (which
  stops); the photos are downloaded again into the new folder and the old folder is left as it is.
- Lightroom: albums land at their path in the album tree (`<set>/<album>`); folders from the flat
  layout move there without a download, and set folders a move empties are removed.
- Photos keep their colour profile (ICC); wide-gamut photos, such as iPhone originals, no longer shift in colour.

## 0.2.0 — 2026-11-22

- Add `AlbumsManager.frame_api`, a versioned in-process facade (`albums`, `check`, `add`,
  `remove`, `sync`) for the paired Frame device. Services, options and sync behaviour are
  unchanged. While the entry unloads, its `check`, `add`, `remove` and `sync` raise
  `photos_unavailable` and change nothing.
- The share client's User-Agent is `domosenso-photos/0.2.0`.

## 0.1.2 — 2026-11-21

- Own brand icon (a photo frame), with dark variants and @2x sizes, in place of the Frame icon.

## 0.1.1 — 2026-11-21

- The albums sensor no longer keeps showing `syncing` after a sync finishes: it is updated again
  once the album's lock is released.
- The integration no longer closes Home Assistant's managed aiohttp session on unload (HA logged
  a warning); HA detaches it with the entry.

## 0.1.0 — 2026-11-21

- Sync shared Synology Photos albums into a photo folder for a slideshow. Each album is added by
  its share link; photos are converted to screen-sized JPEG files (HEIC supported through
  `pillow-heif`), GPS location is always removed, and the link password stays in `.storage`.
- Add the `album_add`, `album_remove` and `album_sync` actions (administrators only, with
  responses) and `sensor.domosenso_photos_albums`.
- Hold deletions that would remove more than half of an album and ask for confirmation.
- Install `pillow-heif` 1.8.1 from the entry setup, in the background, instead of a manifest
  requirement: without network the entry still loads and converts HEIC photos from the share's
  thumbnail (`heic: thumbnails`).
- `album_add` takes no password, because service data is recorded in `call_service` events; a
  link with a password is added in the options form.
- Sync Live Photos as photos; fall back to the share's thumbnail when an original cannot be
  decoded, and retry skipped photos at most once a day while the album is unchanged.
- One album syncs at a time; redirects are not followed, a response is limited to 200 MB and a
  request to 15 minutes; a missing photo folder is reported as `photo_dir_missing` and never
  created again.
- Skip the photo listing while the album's version, name and local files are unchanged, with a
  full listing at least once a day.
