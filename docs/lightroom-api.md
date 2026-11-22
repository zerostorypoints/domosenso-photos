# Lightroom API: what a real account returns

Read on 2026-11-22 with a throwaway, read-only probe (GET requests only towards `lr.adobe.io`; one
`POST` to Adobe's token endpoint for the sign-in) on one cloud Lightroom account: the first 200
image assets, every album, one album's members. Only field names and counts were recorded, never
values; the examples below are invented. **Read** marks what the probe saw, **inference** what it
did not.

## Decision record (plan Task 1)

| decision | value | basis |
|---|---|---|
| `REDIRECT` | `my` | Adobe's console accepted `https://my.home-assistant.io/redirect/oauth`; a sign-in through Home Assistant came back to the instance with a code (read) |
| `FULLSIZE` | `rarely` | `GET renditions/fullsize` answered `404` for 20 of 20 assets (read): photos are at most 2048 px in practice |
| `DIMENSIONS` | `none` (orientation filter stays hidden) | `payload.develop.croppedWidth/croppedHeight` and `payload.importSource.originalWidth/originalHeight` exist on every asset, but how they combine with `develop.userOrientation` and `xmp.tiff.Orientation` was not checked (inference) |
| `KEYWORDS` | `none` (keyword filter stays hidden) | no keyword field on any asset read |
| `CHANGED` | `combined`: `develop.userUpdated` + `payload.userUpdated` | the edit check was inconclusive (the edited photo was not among the assets read), so the stamp combines both; a rating or flag change then also re-fetches that one photo |
| `EDITED` | `payload.develop.fromDefaults` is false | `develop.userUpdated` is present on almost every asset, edited or not (read), so it cannot mean "edited"; `fromDefaults` is present on every asset (meaning: inference) |
| `ACCOUNT_CLAIM` | `id_token` `sub` | present, and equal to the access token's `user_id` (read) |
| renditions | served directly | `GET renditions/2048` answered `200` with redirects disabled (read) |

## Sign-in (Adobe IMS)

- Token response keys: `access_token`, `refresh_token`, `id_token`, `token_type`, `expires_in` (read).
- The refresh token is a JWT whose claims carry `created_at` and `expires_in`, in milliseconds, as
  strings; `expires_in` was `1209600000` (14 days) (read).
- Two sign-ins on one client: not checked yet; a separate Developer Console project per tool
  avoids the question.
- Renewal: a refresh token has been seen to be replaced by a new one on refresh once it is about a
  week old (another tool's history on one account; inference).

## Lightroom (`https://lr.adobe.io`)

- Every JSON body starts with `while (1) {}` and a newline.
- Lists carry `base` and `links.next.href` (relative to `base`) while more pages exist.
- `GET /v2/catalog`, `/v2/catalogs/{c}/assets?subtype=image&limit=100`, `/v2/catalogs/{c}/albums`,
  `/v2/catalogs/{c}/albums/{a}/assets` (read).
- Album `subtype` values: `collection`, `collection_set` and `smart` (read). Smart albums are
  ignored. Nesting: `payload.parent.id` (read on nearly all albums).
- Renditions offered on every asset (links read): `thumbnail2x`, `640`, `1280`, `2048`. `fullsize`
  usually does not exist (above).

## Asset fields (coverage over the assets read)

| field | present |
|---|---|
| `payload.captureDate`, `payload.importSource.importTimestamp`, `payload.userCreated`, `payload.userUpdated`, `created`, `updated` | all |
| `payload.ratings.<app>.rating` / `.date`, `payload.reviews.<app>.flag` / `.date` | almost all (absent when never rated or flagged) |
| `payload.develop.userUpdated` | almost all |
| `payload.develop.fromDefaults`, `croppedWidth`, `croppedHeight`, `userOrientation` | all |
| `payload.importSource.originalWidth`, `originalHeight` | all |
| `payload.xmp.tiff.Make` / `Model`, `payload.xmp.aux.Lens`, `payload.xmp.exif.FocalLength` / `FNumber` / `ExposureTime` / `ISOSpeedRatings` | almost all |
| `payload.xmp.aux.SerialNumber` | almost all — never written to a photo |
| `payload.xmp.mwg-rs` (face regions with person ids) | a few — never written to a photo |
| keywords | none |

Example (invented): `"captureDate": "2024-05-03T17:03:26.84+02:00"`,
`"ratings": {"c0ffee…": {"rating": 4, "date": "2024-05-04T08:00:00Z"}}`.
