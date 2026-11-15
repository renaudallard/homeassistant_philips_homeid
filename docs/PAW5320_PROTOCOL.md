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
| Devices of a home | `GET /api/homes/{home}/devices` → `id`, `vendorId`, `externalId`, `productCtn`, `name` |
| Meals of a home | `GET /api/homes/{home}/meals` |
| Events | `GET /api/homes/{home}/events?from=…&to=…&types=…&clustered=false` |
| Device settings | `GET /api/homes/{home}/devices/{device}/settings` |

## Identity

Three different identifiers exist, and none substitutes for another:

| Name | Where | Meaning |
|---|---|---|
| vendor ID | HomeID appliance `externalDeviceId`; pet registry `vendorId` | Tuya device ID |
| Philips ID | pet registry `id`; meal and event `deviceId` | pet backend's own device ID |
| registry external ID | pet registry `externalId` | optional; absent on the live PAW5320 |

- Discovery (config flow, read only): the HomeID appliance list has no IoT
  thing for the feeder. The integration then looks for a pet registry device
  whose `vendorId` equals the appliance's `externalDeviceId` exactly and whose
  `productCtn` model is `PAW5320`. Names and ID prefixes never count. Two
  matches are an error, not a guess. *Live:* discovery of a PAW5320.
- Meals belong to the feeder when `deviceId` equals its Philips ID.
- Events: *live* `meal_dispensed` events carry `deviceId` = Philips ID and
  `deviceExternalId` = the vendor ID, while the registry has no `externalId`.
  While the registry has no `externalId`, only that exact pair is accepted:
  the Philips ID alone, the vendor ID alone, or either one paired with a
  different ID is unverifiable. The pair never overrides a known, different
  registry `externalId`. Events that positively
  name another device are skipped. An event that matches this feeder by one ID
  and contradicts it by the other, or that cannot be attributed, makes that
  read unavailable instead of silently empty.

## Schedules

Meal fields: `id`, `deviceId`, `name`, `portionAmount` (older rows: `amount`),
`feedTime` (older rows: `time`), `repeatDays`, `enabled`. *Live:* reading.

- `feedTime` is `HH:MM` in the home's IANA `timeZone`, or `HH:MMZ` for UTC.
  Weekdays are 1 = Monday … 7 = Sunday, in the same zone as the time. Disabled
  meals and meals with no repeat days are kept, never dropped or rewritten. No
  Home Assistant time zone is substituted for a missing home zone.
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
`deviceExternalId`, and for `meal_dispensed` also `mealName`, `mealAmount`,
`source`. `url`, `mealUrl`, thumbnails and `metadata` are ignored.

- History requests only `types=meal_dispensed`, in windows of at most 31 days
  (the history calendar splits up to 93 days). Nothing is synthesised from
  schedules or commands. `mealAmount` has no documented unit and is shown as
  a plain number. *Live:* reading with `clustered=false`. Whether results are
  paginated or capped has not been observed.
- Status requests `types=food_level_low,food_outlet_stuck,device_online,device_offline`
  over the last 7 days, separately from history so that a status event that
  cannot be attributed does not hide the feeding history. The integration keeps
  the latest time per type. These are one-shot reports: no event says that
  food was refilled or an outlet cleared, so no healthy state is inferred and
  the alerts are timestamps. Connectivity is whichever of `device_online` /
  `device_offline` came last, unknown if neither occurred in the window.

## Desiccant timer

`GET …/devices/{Philips ID}/settings` returns objects of the form
`{"type": …, "value": <ISO 8601>, "format": "datetime"}` under
`filter_replacement_time` and `filter_application_time` (shape from
`petsseries`, where the fountain's filter uses it). The integration reports
`filter_replacement_time` as **Desiccant replacement due** and
`filter_application_time` as its `last_replaced` attribute. Absent or empty
values stay unknown; any other shape makes the sensor unavailable.

## Verification status

| Area | Status |
|---|---|
| Discovery through the pet registry | live, read only |
| Reading meals, home time zone | live, read only |
| `meal_dispensed` history and its identity pair | live, read only |
| `food_level_low`, `food_outlet_stuck`, `device_online`, `device_offline` on a PAW5320 | **not observed live**; types from `petsseries` |
| Settings response of a PAW5320, and that it is the desiccant timer | **not observed live**; the HomeID app shows no desiccant timer for the test feeder |
| `clustered=false`, pagination, `mealAmount` unit | not established |
| Feeder behaviour at DST changes | not established |

## Open questions

1. Which status event types does a PAW5320 actually emit, and with which
   `deviceId` / `deviceExternalId` pair?
2. Does the PAW5320 settings response contain `filter_replacement_time`, with
   which `type`, and does it match the desiccant date shown in the app?
3. Are event lists paginated or capped for busy homes?
