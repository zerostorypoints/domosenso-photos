# Changes

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
