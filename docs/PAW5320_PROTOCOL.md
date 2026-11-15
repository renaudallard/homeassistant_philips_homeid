# PAW5320 Pet Feeder — HomeID Pet REST Protocol

The Philips Smart Feeder with Camera (PAW5320) is supported by the HomeID app
from version 8.23.0. It is not a FUSION device and has no AWS IoT thing: the
app keeps schedules, events and settings on the HomeID pet backend described
here, and talks to the feeder itself over Tuya. This integration uses only the
REST backend. Feeding on demand needs the Tuya channel and is planned for a
follow-up; the camera is out of scope.

Sources: the integration's own read-only checks against a real PAW5320 (noted
as *live* below), the maintainer's check of `GET homes` with and without a
token, and the MIT-licensed [`petsseries`](https://github.com/AboveColin/petsseries)
client (v1.2.0) for request and event shapes. `petsseries` is a reference, not
a dependency.

## Authentication

- Base URL: `https://petseries.production.nbx.iot.versuni.com/api/`
- `Authorization: Bearer <access token>`, where the token is the same HomeID
  OIDC access token used for the HomeID appliance list. `GET homes` answers
  200 with it and 401 without it.
- HomeID refresh tokens are single use: refreshing returns a new one and
  retires the old. Exactly one owner per config entry may spend it. In the
  integration that is `FeederTokenOwner` (`feeder_api.py`): one per entry,
  kept in `hass.data` so setup retries and reloads share its lock, cached
  access token, in-flight refresh and back-off. The rotated token is written to
  the config entry before the new access token is used. A refresh that
  finishes after a reauth stored a newer login is discarded rather than
  written over it. The config flow only uses the access token from the login
  itself and never refreshes.
- The access token is cached until a minute before `expires_in`. A read that
  gets 401/403 on a token older than five minutes refreshes once and retries
  once; a 401 on a younger token is reported without spending another refresh.

## Requests

The integration only reads: its transport refuses every method but `GET`.
Only fixed paths under `/api/homes` on the fixed host are requested. URLs in
responses (`url`, `mealUrl`, `Location`, media links) are never followed and
redirects are refused (`allow_redirects=False`). Errors are re-raised without
their cause, because aiohttp errors carry the request URL and the token
endpoint's errors carry server text. Lists use the envelope `{"item": [...]}`.

| Purpose | Request |
|---|---|
| Homes | `GET /api/homes` → `id`, `timeZone` (IANA name) |
| Devices of a home | `GET /api/homes/{home}/devices` → `id`, `vendorId`, `productCtn`, `name` |
| Meals of a home | `GET /api/homes/{home}/meals` |
| Events | `GET /api/homes/{home}/events?from=…&to=…&types=…&types=…&deviceIds=…&clustered=false` |
| Desiccant timer | `GET /api/homes/{home}/devices/{device}/desiccant-timer` |

## Identity

Two different identifiers exist, and neither substitutes for the other:

| Name | Where | Meaning |
|---|---|---|
| vendor ID | HomeID appliance `externalDeviceId`; pet registry `vendorId` | Tuya device ID |
| Philips ID | pet registry `id`; meal and event `deviceId` | pet backend's own device ID |

- Discovery (config flow, read only): the HomeID appliance list has no IoT
  thing for the feeder. The integration then looks for a pet registry device
  whose `vendorId` equals the appliance's `externalDeviceId` exactly and whose
  `productCtn` model is `PAW5320`. Names and ID prefixes never count. Two
  matches are an error, not a guess. A home whose devices cannot be read is
  skipped when the feeder is found in another; with no match anywhere its
  error is raised, since the answer is then unknown. *Live:* discovery of a
  PAW5320.
- Meals belong to the feeder when `deviceId` equals its Philips ID.
- Events: the integration asks only for this feeder's events
  (`deviceIds` = its Philips ID) and takes an event as the feeder's when its
  `deviceId` is the Philips ID, as the HomeID app 8.23.0 does. The app's event
  model also has `deviceVendorId` but never reads it; *live* `meal_dispensed`
  events carry both `deviceVendorId` and `deviceExternalId`, each equal to
  the vendor ID. Neither is used.
  Events naming another device or none are skipped.

## Schedules

Meal fields: `id`, `deviceId`, `name`, `portionAmount` (older rows: `amount`),
`feedTime` (older rows: `time`), `repeatDays`, `enabled`. *Live:* reading.

- `feedTime` is `HH:MM` in the home's IANA `timeZone`, or `HH:MMZ` for UTC.
  Weekdays are 1 = Monday … 7 = Sunday, in the same zone as the time. Disabled
  meals and meals with no repeat days are kept, never dropped or rewritten.
- The app's home model has no `timeZone`: it shows meal times in the phone's
  zone, and its onboarding stores that zone on the home with `PATCH
  homes/{home}`. *Live:* the home carried it. When it is missing, the
  integration uses the Home Assistant time zone instead.
- Calendar occurrences are predictions: nonexistent local times in a DST gap
  are skipped, a repeated hour uses its first instant. What the feeder itself
  does at DST changes is unknown.
- Editing schedules is not part of this version; meals are changed in the
  HomeID app. For a later change: `petsseries` creates meals with `POST
  …/meals` (`deviceId`, `feedTime`, `name`, `portionAmount`, `repeatDays`;
  201 with `Location`), edits and enables/disables them with `PATCH
  …/meals/{meal}` and deletes with `DELETE`. None of this has been tried on a
  feeder. aiohttp resends an idempotent `DELETE` by itself after a dropped
  connection, so a write path needs a guard against that.

## History and status events

Event fields used: `id`, `type`, `time` (ISO 8601 with offset), `deviceId`,
and for `meal_dispensed` also `mealName`, `mealAmount`, `source`. An event
whose `time` is not ISO 8601 with an offset is skipped, as in the app. `url`, `mealUrl`, thumbnails and `metadata` are ignored.

- History requests only `types=meal_dispensed`, in windows of at most 31 days
  (the history calendar splits up to 93 days). Nothing is synthesised from
  schedules or commands. `mealAmount` has no documented unit and is shown as
  a plain number. *Live:* reading with `clustered=false`. Whether results are
  paginated or capped has not been observed.
- Status requests `food_level_low`, `food_outlet_stuck`, `device_online` and
  `device_offline`, one `types` parameter each as the app's Retrofit client
  encodes a list (the app itself never filters by type), over the last 7
  days, separately from history so that a status event that cannot be
  attributed does not hide the feeding history. The integration keeps
  the latest time per type. These are one-shot reports: no event says that
  food was refilled or an outlet cleared, so no healthy state is inferred and
  the alerts are timestamps. Connectivity is whichever of `device_online` /
  `device_offline` came last, unknown if neither occurred in the window.

## Desiccant timer

`GET …/devices/{Philips ID}/desiccant-timer` returns `expiryDate` and
`lastReplacedDate`, both ISO dates (`YYYY-MM-DD`), as the HomeID app 8.23.0
reads them. The integration follows the app: a 404, or an `expiryDate` that
is missing or not an ISO date, means no timer and the sensor is unknown; a
`lastReplacedDate` that is not an ISO date is left out. `expiryDate` is
reported as **Desiccant replacement due** and `lastReplacedDate` as its
`last_replaced` attribute. The device settings
(`…/devices/{Philips ID}/settings`) hold only `feeder_voice_audio_id` in the
app and are not read.

## Verification status

| Area | Status |
|---|---|
| Discovery through the pet registry | live, read only |
| Reading meals, home time zone | live, read only |
| `meal_dispensed` history | live, read only |
| `food_level_low`, `food_outlet_stuck` on a PAW5320 | live, read only |
| `device_online`, `device_offline` on a PAW5320 | **not observed live**: none in a 7 day window; types from `petsseries` |
| Desiccant timer response of a PAW5320 | live, read only: `expiryDate` and `lastReplacedDate`; not yet compared with the app screen |
| `clustered=false`, pagination, `mealAmount` unit | not established |
| Feeder behaviour at DST changes | not established |

## Open questions

1. Does a PAW5320 emit `device_online` and `device_offline`, for example
   after a power cut?
2. What does the desiccant timer return for a PAW5320 that never had one
   set, a 404 or an empty object, and does `expiryDate` match the date shown
   in the app?
3. Are event lists paginated or capped for busy homes?
