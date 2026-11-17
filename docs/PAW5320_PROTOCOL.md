# PAW5320 Pet Feeder: HomeID Pet REST and Tuya Feed Now

The Philips Smart Feeder with Camera (PAW5320) is supported by the HomeID app
from version 8.23.0. It is not a FUSION device and has no AWS IoT thing: the
app keeps schedules, events and settings on the HomeID pet backend described
here, and talks to the feeder itself over Tuya. This integration reads the
REST backend and sends feed now over Tuya, as the app does; the camera is out
of scope.

Sources: the integration's own checks against a real PAW5320, read only apart
from two feed now presses (noted as *live* below), the maintainer's check of
`GET homes` with and without a token, and the MIT-licensed
[`petsseries`](https://github.com/AboveColin/petsseries) client (v1.2.0) for
request and event shapes. `petsseries` is a reference, not a dependency.

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

The REST transport only reads: it refuses every method but `GET`.
Only fixed paths under `/api/homes` on the fixed host are requested. URLs in
responses (`url`, `mealUrl`, `Location`, media links) are never followed and
redirects are refused (`allow_redirects=False`). A reply is read up to 4 MiB
and, if gzip encoded, inflated by the integration to at most 4 MiB, since
aiohttp sets no limit on a whole reply; other encodings are refused. Errors
are re-raised without their cause, because aiohttp errors carry the request
URL and the token endpoint's errors carry server text. Lists use the envelope
`{"item": [...]}`.

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
  a plain number; after a feed now of 1 and of 2 portions it was 1 and 2,
  nothing was weighed. *Live:* reading with `clustered=false`. Whether
  results are paginated or capped has not been observed.
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

## Feed now (Tuya)

The app feeds through the Tuya (ThingClips) SDK it bundles, not through the
pet backend. The integration signs the same requests itself (`tuya_api.py`,
on hashlib, hmac and cryptography), so nothing extra is installed.

- Login, as `TuyaAuthClientImpl`: `thing.m.user.third.login` with
  `countryCode`, `accessToken` = the HomeID OIDC id token, `type` `jwt` and
  `extraInfo` `{"platform":"PhilipsDA"}`. The app's `countryCode` is the
  `dialCode` of the HomeID space chosen in it, `31` if none. The integration
  takes the space of `/.well-known/tenant/oneka` whose `countryCode` is Home
  Assistant's country, and `31` otherwise. The id token comes from the same
  refresh as the access token, through `FeederTokenOwner`, so the refresh
  token is still spent once; a refresh returns a new id token valid for an
  hour.
- Transport: form POSTs to `https://a1.tuyaeu.com/api.json`, then to the
  login reply's `domain.mobileApiUrl` when that is one of Tuya's regional
  hosts (no other host is accepted, redirects are refused). `sign` is
  HMAC-SHA256 over the sorted signed parameters, `postData` is AES-GCM sealed
  with a key derived from the request ID and the session's `ecode`, and the
  reply comes back sealed the same way. A reply is read up to 1 MiB and,
  if gzip encoded, inflated by the integration to at most 1 MiB, not by
  aiohttp, whose older releases inflate without a limit; other encodings are
  refused. Tuya was seen answering with no `Content-Encoding`. The app
  identity used for signing (app ID and secret from the APK manifest, app
  key and certificate hash from `petsseries`) is the same in every copy of
  the HomeID app.
- Feed: `thing.m.device.dp.publish` with `devId` = `gwId` = the vendor ID and
  `dps` `{"201": n}`. `n` is 1 to 20, the range `QuickFeedApiImpl` clamps to;
  201 is the data point of a feeder with camera
  (`SingleContainerDispenseSerializer`). Feeders without camera use 101 and
  the dual container 236; neither model is supported here.
- Session: one per entry, made on the first press and kept in memory. A
  session made during development still answered after 25.8 hours, and a
  second login did not end the first. `petsseries` reports a daily login cap
  (`USER_SESSION_LIMIT`), so the integration logs in again only when Tuya
  says the session is gone.
- One press sends the feed once. Tuya answers a session it no longer knows
  with `USER_SESSION_LOSS`, or with a reply that opens with neither key, and
  the latter cannot be told from other unreadable replies: Tuya may have
  carried out the feed. So after either, the integration never sends the
  feed again. After `USER_SESSION_LOSS`, or `USER_SESSION_INVALID` which
  the Tuya SDK reads the same way, it drops the session, so the next
  press logs in again. After an unreadable reply it checks the session with
  a harmless read (`m.life.home.space.list`) and drops it only if that read
  fails too.
- Every opened reply seen live carries `success` and `status`: `true` and
  `ok` on a session read, `false` and `error` with `errorCode` and
  `errorMsg` on a refused publish. A reply that does not say `success: true`
  is not taken as done. A login and an accepted `dp.publish` both answered
  with the keys `result`, `status`, `success` and `t`, and the publish
  passed that check, so its `success` was `true`. The integration logs the
  key names of each reply at debug level.
- A publish that Tuya accepts says only that the cloud took the command;
  whether food came out shows in the history as `meal_dispensed`.
- *Live* on a tester's PAW5320 with build 090a418: a press of 1 portion,
  then one of 2 portions 90 seconds later in the same Home Assistant run.
  Each dispensed what was asked and showed as one `meal_dispensed` event
  with `source` `device`, 8 and 12 seconds after the press. The HomeID app
  on the phone stayed signed in. How many Tuya logins the two presses took
  was not seen.
- *Live* on the same feeder with build 6767ad8 and the debug log on: one
  press of 1 portion dispensed and showed as one `meal_dispensed` event
  with `source` `device` and `mealAmount` 1, about 9 seconds after the
  press. The log had one login reply and one publish reply.

## Verification status

| Area | Status |
|---|---|
| Discovery through the pet registry | live, read only |
| Reading meals, home time zone | live, read only |
| `meal_dispensed` history | live, read only |
| `food_level_low`, `food_outlet_stuck` on a PAW5320 | live, read only |
| `device_online`, `device_offline` on a PAW5320 | **not observed live**: none in a 7 day window; types from `petsseries` |
| Desiccant timer response of a PAW5320 | live, read only: `expiryDate` and `lastReplacedDate`; not yet compared with the app screen |
| `clustered=false`, pagination, `mealAmount` unit | not established; `mealAmount` equalled the portions in two feeds |
| Feeder behaviour at DST changes | not established |
| Tuya login, session reuse and lifetime with the integration's signer | live, maintainer account without a feeder |
| `dp.publish` signature and ownership check | live: `PERMISSION_DENIED` for a device not on the account |
| Reply wrapper `success`, `status` | live on a session read, a refused publish, a login and an accepted publish (build 6767ad8) |
| Feed now `{"201": n}` on a PAW5320 | live: 1 and 2 portions dispensed, one `meal_dispensed` each with `source` `device` (build 090a418) |
| Feed now session over a day, expired session, failure paths | not established |

## Open questions

1. Does a PAW5320 emit `device_online` and `device_offline`, for example
   after a power cut?
2. What does the desiccant timer return for a PAW5320 that never had one
   set, a 404 or an empty object, and does `expiryDate` match the date shown
   in the app?
3. Are event lists paginated or capped for busy homes?
4. Does a HomeID account outside Tuya's EU region log in at the EU host, or
   is it moved to another one?
