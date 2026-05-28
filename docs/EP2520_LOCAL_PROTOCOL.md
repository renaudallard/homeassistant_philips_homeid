# EP2520 / EP-SM Espresso Machines — Local Control Protocol

This documents the **local** (LAN) control protocol for Philips/Saeco EP and SM
series espresso machines, confirmed on an **EP2520** (`Flash_Entry_P`, sold as
"Series 3200", CTN `EP2520/10`, sw `00.06.04`). Read support landed in #20; this
covers **power** and **brew** control.

These machines speak the same `PHILIPS-Condor` HTTPS protocol as the airfryers
(see `APK_DECOMPILED_ANALYSIS.md` §0 for the challenge-response auth and TLS
notes). They stay reachable on the LAN **while in standby**, and — importantly —
the HomeID app drives power/brew over this **local** path, not the cloud. There
is **no certificate pinning on the local device connection**, so the protocol
below was captured with a plain proxy (mitmproxy) and the app's normal traffic.

All requests are authenticated with the cached `Authorization: PHILIPS-Condor …`
header and target product id `1` unless noted.

## Ports

| Port | Method | Purpose |
|------|--------|---------|
| `/di/v1/products/1/machinestatus` | GET | Machine state (`mainstate`, `brewstat`, levels, errors) |
| `/di/v1/products/1/configuration` | GET | Config (`recipelist`, `remotectrl`, water hardness, …) |
| `/di/v1/products/1/command` | GET/PUT | Power + brew command port |
| `/di/v1/products/1/command/BasicRecipe` | PUT | Start a brew (recipe sub-resource) |
| `/di/v1/products/1/device` | GET | Device info (name, type, CTN, sw version) |
| `/di/v1/products/0/firmware` | GET | Firmware info |

### `command` port

```json
{ "power": 0, "processctrl": 0, "BasicRecipe": {} }
```

- **`power`** is a *mode enum, not a boolean*. It is **momentary** — the device
  consumes the written value and the field reads back as `0` afterwards.
  - `2` → **power ON** (machine wakes; from cold it runs a rinse)
  - `1` → **power OFF / standby**
  - `0` → idle (the readback / no-op value)
- **`processctrl`** — brew process control (not yet decoded; left at `0`).
- **`BasicRecipe`** — the recipe currently being brewed (empty when idle).

Power on: `PUT /di/v1/products/1/command  {"power": 2}`
Power off: `PUT /di/v1/products/1/command  {"power": 1}`

### `machinestatus.mainstate`

| value | meaning |
|------|---------|
| 1 | standby (off) |
| 2 | ready |
| 3 | brewing |
| 4 | processing (heating / rinsing) |
| 5 | action required (e.g. refill) |

> Note: `mainstate == 1` is "off" from the user's perspective, even though the
> device still answers on the network. A power/on indicator should treat
> `mainstate >= 2` as "on".

## Brewing

Brewing is a **`PUT` to the `command/BasicRecipe` sub-resource**, sent **after**
the machine is powered on and `mainstate == 2` (ready). The HomeID app powers on,
polls `machinestatus` until ready, then writes the recipe.

```
PUT /di/v1/products/1/command/BasicRecipe
{
  "RecipeBookId": 2,     // drink id (see recipelist)
  "GrDose": 2,           // grind / coffee dose (strength)
  "PrimDose": 40,        // primary water volume, ml
  "SecDose": 0,          // secondary volume (2nd cup / milk), ml
  "Temperature": 2,      // temperature setting
  "NrOfBrews": 0,        // 0 = single
  "randnr": 1328873381   // per-request random nonce
}
```

### Drink ids (`RecipeBookId`)

`configuration.recipelist` enumerates the drinks the machine offers (`255` = empty
slot). On the EP2520 this was `[2, 6, 21, 26, 255, 255]`. Ids match the APK
`RitaDrinkKt` enum. Confirmed by capture:

| RecipeBookId | Drink | PrimDose (ml) captured |
|--------------|-------|------------------------|
| 2 | Espresso | 40 |
| 6 | Coffee | 120 |
| 21 | Hot Water | — (per APK `RitaDrinkKt`) |
| 26 | (machine-specific) | — |

`PrimDose` is the water volume in ml and is the main per-drink difference
(espresso 40 ml vs long coffee 120 ml); the other fields were identical across
the two captured drinks.

## Captured examples

Espresso:
```
PUT /di/v1/products/1/command          {"power": 2}
PUT /di/v1/products/1/command/BasicRecipe
    {"RecipeBookId":2,"GrDose":2,"PrimDose":40,"SecDose":0,
     "Temperature":2,"NrOfBrews":0,"randnr":1328873381}
```

Coffee (long):
```
PUT /di/v1/products/1/command/BasicRecipe
    {"RecipeBookId":6,"GrDose":2,"PrimDose":120,"SecDose":0,
     "Temperature":2,"NrOfBrews":0,"randnr":1767779466}
```
