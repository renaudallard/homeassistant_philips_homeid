# Philips HomeID protocol notes

These notes describe how the Philips HomeID app (Android, version 8.16.0) talks to Philips kitchen and air appliances, on the local network and through the cloud. They were worked out by studying the app and checked against real devices where noted. They are written as a protocol description; no code from the app is reproduced here. App class and package names are given only as pointers for anyone who needs to look something up again.

Some of what is described here is not used by this integration. In particular the HSDP cloud relay and its token exchange are documented because the app still carries them, but the integration does not use HSDP at all: FUSION devices go through the DaConnect/Versuni MQTT relay described under "End-to-end flows" and "Cloud login".

## Contents

1. [Quick start: talking to a device](#quick-start-talking-to-a-device)
2. [Architecture](#architecture)
3. [Device model](#device-model)
4. [LAN pairing](#lan-pairing)
5. [PhilipsCondor request authentication](#philipscondor-request-authentication)
6. [LAN requests](#lan-requests)
7. [Encryption key exchange](#encryption-key-exchange)
8. [Payload encryption](#payload-encryption)
9. [TLS and certificate pinning](#tls-and-certificate-pinning)
10. [LAN transport setup](#lan-transport-setup)
11. [Discovery](#discovery)
12. [UDP event subscriptions](#udp-event-subscriptions)
13. [Transport strategies](#transport-strategies)
14. [Error codes](#error-codes)
15. [Polling and connectivity](#polling-and-connectivity)
16. [HSDP cloud relay](#hsdp-cloud-relay)
17. [Message keys](#message-keys)
18. [HSDP authentication](#hsdp-authentication)
19. [DaConnect authentication](#daconnect-authentication)
20. [MQTT connection info](#mqtt-connection-info)
21. [Credential storage](#credential-storage)
22. [Crypto helpers](#crypto-helpers)
23. [Protocol summary](#protocol-summary)
24. [Appliances and ports](#appliances-and-ports)
25. [Port reference](#port-reference)
26. [HSDP pairing flow](#hsdp-pairing-flow)
27. [Firmware port](#firmware-port)
28. [End-to-end flows](#end-to-end-flows)
29. [Cloud login (Gigya OIDC)](#cloud-login-gigya-oidc)
30. [Other app classes](#other-app-classes)
31. [Appendix: obfuscated name map](#appendix-obfuscated-name-map)
32. [Appendix: app source map](#appendix-app-source-map)
33. [Appendix: corrections](#appendix-corrections)

## Quick start: talking to a device

This section covers the minimum needed to read and control a Philips airfryer or air purifier on the local network with plain HTTPS requests. No SDK, Android device or cloud account is involved.

### What you need

- The device IP address. Devices announce themselves over mDNS as `_philipscondor._tcp.local.` and over SSDP as `urn:philips-com:device:DiProduct:1`.
- A `client_id`: 16 random bytes, base64 encoded. You generate it once and keep it.
- A `client_secret`: a base64 string the device hands out during pairing.

### Step 1: generate a client_id

```python
import base64
import os

client_id = base64.b64encode(os.urandom(16)).decode()
# for example "fTJdQStrzUhafYGycn7Jtw=="
```

### Step 2: pair

Send the client_id to the auth endpoint. Certificate validation can be skipped at this point (see TLS below).

```
PUT https://{ip}/auth/v1/
Content-Type: application/json

{"id": "fTJdQStrzUhafYGycn7Jtw=="}
```

A device that is not paired yet answers:

```json
{"authenticated": true, "secret": "Kx1eBqP8SvI6fYGycn7abc=="}
```

Store `secret` as the `client_secret`. Pairing is a one-time step.

A device that is already paired to another client answers with a seed instead of a secret:

```json
{"authenticated": false, "seed": "someSeedValue"}
```

The seed challenge is described in the pairing section of this document. If you cannot answer it with the credentials of the existing pairing, a factory reset of the device clears it.

### Step 3: first request and the challenge

Every request after pairing is authorized with the `PHILIPS-Condor` challenge-response scheme. A request without credentials is refused:

```
GET https://{ip}/di/v1/products/1/airfryer
Connection: keep-alive
```

```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: PHILIPS-Condor tx7ShrFB7mlC26GQ/mSeIw==
```

The value after `PHILIPS-Condor ` is a base64 challenge that decodes to 16 bytes.

### Step 4: answer the challenge

1. Base64 decode the challenge, the client_id and the client_secret. Each is 16 bytes.
2. Concatenate them in this order: challenge, client_id, client_secret (48 bytes).
3. Take the SHA-256 digest of those 48 bytes (32 bytes).
4. Concatenate the raw client_id and the digest (48 bytes) and base64 encode the result.
5. Send it as `Authorization: PHILIPS-Condor <result>`.

```python
import base64
import hashlib


def condor_credentials(www_authenticate: str, client_id: str, client_secret: str) -> str:
    challenge = base64.b64decode(www_authenticate.split(" ", 1)[1])
    cid = base64.b64decode(client_id)
    secret = base64.b64decode(client_secret)
    digest = hashlib.sha256(challenge + cid + secret).digest()
    return "PHILIPS-Condor " + base64.b64encode(cid + digest).decode()
```

Because the client_id comes first, the start of the encoded credentials matches the encoded client_id (64 characters in total, see the example below).

### Step 5: retry with credentials

```
GET https://{ip}/di/v1/products/1/airfryer
Connection: keep-alive
Authorization: PHILIPS-Condor fTJdQStrzUhafYGycn7Jtxedal6/TLLSOsVgUFUo8HDf2UOZ4Bn82Wjw4XW2iELG
```

```json
{
  "status": "standby",
  "temp": 0,
  "time": 0,
  "cur_time": 0,
  "drawer_open": false,
  "preset": 0,
  "error": 0,
  "temp_unit": false,
  "step_id": "",
  "recipe_id": ""
}
```

Keep the `Authorization` value and send it with every following request. When a request comes back 401 again, answer the new challenge and carry on with the new value.

### Step 6: control the device

Writes are a PUT of the properties to change on the same port:

```
PUT https://{ip}/di/v1/products/1/airfryer
Content-Type: application/json
Authorization: PHILIPS-Condor <cached credentials>

{"temp": 180, "time": 600, "status": "cooking"}
```

### Endpoints

| Path | Method | Purpose |
|------|--------|---------|
| `/auth/v1/` | PUT | Pair, body `{"id": "..."}` |
| `/di/v1/products/1/airfryer` | GET | Read airfryer state |
| `/di/v1/products/1/airfryer` | PUT | Change airfryer state |
| `/di/v1/products/1/status` | GET | Read air purifier state |
| `/di/v1/products/1/status` | PUT | Change air purifier state |
| `/di/v1/products/1/air` | GET | Read air quality data |
| `/di/v1/products/1/fltsts` | GET | Read filter status |
| `/di/v1/products/1/device` | GET | Read device info (name, model, firmware) |
| `/di/v1/products/0/security` | GET | Read the AES payload key |
| `/di/v1/products/0/firmware` | GET | Read firmware version info |
| `/di/v1/products/0/pairing` | ExecMethod | Cloud pairing RPC, reached only through HSDP, not over LAN HTTP |

Other airfryer port names in use: `venusaf` (HD9880), `venus1af` (HD9875/76), `nutrimax` (NX0960), `hermesac` (NX0950).

### TLS

- TLS 1.2.
- Certificates are trusted on first use: on the first connection the app stores the SHA-256 of the server public key and checks it on later connections.
- Hostname verification is off, since devices are reached by IP address.
- Cipher suites: `TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256`, `TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256`, `TLS_RSA_WITH_AES_128_CBC_SHA256`.
- Accepting any certificate also works. The app itself has a trust-everything mode (`createTLSTrustEverythingSSLContext`).

### AES payload encryption (UDP events only)

HTTP request and response bodies are plain JSON over HTTPS; the app never encrypts them. AES is only used for the change events a device pushes over UDP to a subscriber (see "UDP event subscriptions"). The key comes from the security port:

```
GET /di/v1/products/0/security
{"key": "abcdef0123456789abcdef0123456789"}
```

The key is 32 hex characters, which is a 16 byte AES-128 key. To decrypt an event payload:

1. Strip surrounding whitespace and base64 decode the payload.
2. Decrypt with AES-128 in CBC mode, IV of 16 zero bytes.
3. Remove the PKCS7 padding.
4. Drop the first 2 bytes, a random prefix the sender adds before encrypting (the app keeps data shorter than 3 bytes as is).
5. Decode the rest as UTF-8 JSON.

```python
import base64

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


def decrypt_event(payload: str, key_hex: str) -> str:
    key = bytes.fromhex(key_hex)
    data = base64.b64decode(payload.strip())
    dec = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).decryptor()
    padded = dec.update(data) + dec.finalize()
    unpadder = PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return plain[2:].decode("utf-8")
```

### Base64

All base64 in the local protocol is the standard alphabet with padding. The app encodes without line breaks (Android flag `NO_WRAP`, value 2) and decodes with the default flags (value 0). Python's `base64.b64encode()` and `base64.b64decode()` behave the same way.

### Full wire example

```
>>> PUT /auth/v1/ HTTP/1.1
>>> Host: 192.168.1.100
>>> Content-Type: application/json
>>> Connection: keep-alive
>>>
>>> {"id":"fTJdQStrzUhafYGycn7Jtw=="}

<<< HTTP/1.1 200 OK
<<< Content-Type: application/json
<<<
<<< {"authenticated":true,"secret":"Kx1eBqP8SvI6fYGycn7abc=="}

>>> GET /di/v1/products/1/airfryer HTTP/1.1
>>> Host: 192.168.1.100
>>> Connection: keep-alive

<<< HTTP/1.1 401 Unauthorized
<<< WWW-Authenticate: PHILIPS-Condor tx7ShrFB7mlC26GQ/mSeIw==

>>> GET /di/v1/products/1/airfryer HTTP/1.1
>>> Host: 192.168.1.100
>>> Connection: keep-alive
>>> Authorization: PHILIPS-Condor fTJdQStrzUhafYGycn7Jtxedal6/TLLSOsVgUFUo8HDf2UOZ4Bn82Wjw4XW2iELG

<<< HTTP/1.1 200 OK
<<< Content-Type: application/json
<<<
<<< {"status":"standby","temp":0,"time":0,"cur_time":0,"drawer_open":false,"preset":0,"error":0}
```

### Partial updates

The app keeps the last known properties of each port (`CondorPort`) and merges new data into them rather than replacing them, whether the data comes from a poll or a push. A push can therefore carry only the fields that changed:

| | Properties |
|---|---|
| Cached | `{"temp": 180, "status": "cooking", "time": 600, "cur_time": 300}` |
| Received | `{"cur_time": 295}` |
| Result | `{"temp": 180, "status": "cooking", "time": 600, "cur_time": 295}` |

### UDP push after subscribing

A subscription is a POST to the port with `subscriber`, `ttl` and `changeudp`. The device then sends UDP datagrams that look like an HTTP request:

```
NOTIFY /di/v1/products/1/airfryer HTTP/1.1\r\n
HOST: 192.168.1.50:8080\r\n
\r\n
<base64 AES encrypted JSON>
```

The body after the blank line is encrypted with the key from the security port, as described above. Once decrypted it is a partial update to merge into the cached state.

## Architecture

The app's connectivity stack has five layers, top to bottom:

1. App layer (`ka.oneka`): UI, domain logic, backend calls, local database.
2. DaConnect SDK (`cl.daconnect`): cloud API, cloud authentication, device control, IoT/MQTT.
3. Condor core (`connectivity.condor.core`): the appliance and `NetworkNode` model, communication strategies, discovery, local store, security, request queue.
4. Transports, side by side:
   - LAN (`connectivity.condor.lan`): HTTPS to the appliance, PhilipsCondor authentication, discovery, payload crypto, TLS pinning.
   - HSDP (`connectivity.condor.hsdp`): cloud relay over MQTT.
5. HSDP client library (`connectivity.hsdpclient`): generated API client and MQTT service.

Every transport exposes the same operations (get, put, add, delete properties on a port, subscribe, unsubscribe). A combined strategy picks the first transport that is currently available, LAN first, cloud second. See "Choosing LAN or cloud" below.

### Package areas worth looking at

| Package | What lives there |
|---------|------------------|
| `connectivity.condor.core` | Device model, local store, security info, serial request queue, combined strategy |
| `connectivity.condor.lan` | HTTPS requests, pairing, PhilipsCondor scheme, SSDP/mDNS discovery, AES decryption, TLS pinning, UDP events |
| `connectivity.condor.hsdp` | Cloud relay transport and its messages |
| `connectivity.hsdpclient` | Generated HSDP API client and MQTT service (the largest of the SDK packages) |
| `cl.daconnect` | DaConnect cloud auth, device control, IoT API |
| `ka.oneka` | Everything else in the app (well over 10,000 files) |

## Device model

The app represents each discovered or paired appliance as a `NetworkNode` record, persisted in SQLite. The Condor protocol version the app speaks is always 1, which is where the `v1` in every LAN URL comes from.

### Stored fields

| Column key | Meaning |
|------------|---------|
| `_id` | SQLite row id |
| `cppid` | Unique device identifier. Used as the key everywhere: local database, cloud API, LAN discovery cache |
| `bootid` | Device boot counter, taken from discovery. A change means the device rebooted |
| `client_id` | Our pairing identity: 128 random bits, base64 encoded, generated by the app on first pairing |
| `client_secret` | Secret the device returns when pairing succeeds; used for every later request |
| `encryption_key` | AES-128 payload key as a 32-digit hex string, fetched from the `security` port |
| `pin` | Base64 SHA-256 of the device's TLS public key, used for certificate pinning |
| `mismatched_pin` | The pin the device presented when it no longer matched `pin` |
| `dev_name` | Friendly name from discovery |
| `device_type` | Model name from discovery (for example `AC2729`) |
| `model_id` | Model number from discovery |
| `model_name` | Deprecated older column for the model name |
| `hsdpid` | HSDP device id used by the cloud relay |
| `https` | Whether the device uses HTTPS (always true in the schema) |
| `ip_address` | Current LAN address |
| `mac_address` | Device MAC address |
| `lastknown_network` | Wi-Fi SSID the device was last seen on |
| `is_paired` | Pairing state, stored as the ordinal below |
| `last_paired` | Timestamp of the last pairing |

Besides the columns, the record holds the cached `Authorization` header value (the PhilipsCondor credentials, regenerated after every 401) and a discovery expiration period that defaults to 15 seconds.

### Pairing states

| Ordinal | State | Meaning |
|---------|-------|---------|
| 0 | PAIRED | Has both `client_id` and `client_secret` |
| 1 | NOT_PAIRED | Never paired (the default for a new record) |
| 2 | UNPAIRED | Was paired, pairing was removed |
| 3 | PAIRING | Pairing in progress |

### Validity and updates from discovery

A record is valid only when `cppid`, the friendly name and the device type are all non-empty. If an IP address is present it must also look like an IP address. Invalid records produced by discovery are dropped.

When discovery reports a device that is already known (same `cppid`), the stored record is refreshed:

- SSID, IP address, name, model id and device type are overwritten when they changed.
- If the reported boot id differs from the stored one and is not -1, the encryption key is cleared and the new boot id is stored. A reboot therefore always forces a new key exchange.
- If the incoming record carries an encryption key, the stored key is cleared as well, so the key is fetched again rather than copied.
- `hsdpid` is only replaced when the new value is non-null.

## LAN pairing

The app does this in `Authentication`.

### Request

```
PUT https://{ip}/auth/v1/
Content-Type: application/json

{"id": "<client_id>"}
```

- The method is PUT. This was confirmed from the deobfuscated APK.
- `id` is the node's `client_id`. If the node has none yet, the app generates 16 random bytes, base64 encodes them, stores the result as `client_id` and uses it from then on.
- The caller may pass extra "evidence" fields, which are merged into the same JSON object next to `id` (this is how the challenge answer `key` is sent, see below).
- The request goes over the same pinned TLS client as every other LAN request. If no LAN network is available the request fails locally with "no wifi connection available".

### Response handling

| Status | Handling |
|--------|----------|
| 200 | Body parsed as a JSON object. `authenticated` (boolean) and `secret` (string) are removed from it. `secret` is stored as `client_secret` only when `authenticated` is true and the secret is non-null. Whatever fields remain (for example `seed`) are passed back to the caller together with the `authenticated` flag |
| 400 | Error `error_bad_request` |
| 502 | Error `error_bad_gateway` |
| other | Error "Request failed - " followed by the response body |

A socket timeout while reading the status is treated as 504 and any other failure there as 502. TLS handshake failures and I/O errors are reported with the exception message. The class also defines an `error_timeout` constant.

### Two pairing patterns

Direct pairing, for a device that accepts a new client:

1. `PUT /auth/v1/` with `{"id": "<client_id>"}`
2. Device answers `{"authenticated": true, "secret": "<client_secret>"}`
3. The app stores the secret.

Seed challenge, for a device that is already paired:

1. `PUT /auth/v1/` with `{"id": "<client_id>"}`
2. Device answers `{"authenticated": false, "seed": "<seed>"}`
3. The app computes `key = SHA256(seed + client_id)` and sends `PUT /auth/v1/` again with `{"id": "<client_id>", "key": "<evidence>"}`
4. Device answers `{"authenticated": true, "secret": "<new_secret>"}`

## PhilipsCondor request authentication

After pairing, every LAN request is authorized with the HTTP authentication scheme `PHILIPS-Condor` (the app's `PhilipsCondorScheme` and `LanRequest`). The scheme is challenge and response, driven by 401 replies.

### Computing the credentials

Input is the value of the `WWW-Authenticate` header from a 401 reply.

1. Remove the `PHILIPS-Condor ` prefix, matched case-insensitively, including the trailing space.
2. Base64-decode the rest. The challenge must be exactly 16 bytes, otherwise no credentials are produced.
3. Both `client_id` and `client_secret` must be present. Base64-decode each to raw bytes.
4. `digest = SHA-256(challenge || client_id_bytes || client_secret_bytes)`
5. `Authorization` value is `PHILIPS-Condor ` followed by `base64(client_id_bytes || digest)`.

The resulting header value is cached on the node and sent with every request until the device answers 401 again.

Illustration in Python:

```python
import base64
import hashlib


def condor_authorization(www_authenticate: str, client_id: str, client_secret: str) -> str | None:
    value = www_authenticate
    if value.lower().startswith("philips-condor "):
        value = value[len("philips-condor "):]
    challenge = base64.b64decode(value)
    if len(challenge) != 16 or not client_id or not client_secret:
        return None
    cid = base64.b64decode(client_id)
    secret = base64.b64decode(client_secret)
    digest = hashlib.sha256(challenge + cid + secret).digest()
    return "PHILIPS-Condor " + base64.b64encode(cid + digest).decode()
```

### Exchange on the wire

```
GET /di/v1/products/1/<port>
Authorization: PHILIPS-Condor <cached credentials, if any>

HTTP 401
WWW-Authenticate: PHILIPS-Condor <base64 of 16 random bytes>

GET /di/v1/products/1/<port>          (same request, sent again)
Authorization: PHILIPS-Condor <base64(client_id_bytes || digest)>

HTTP 200
```

## LAN requests

### URL and methods

```
https://{ip}/di/v1/products/{productId}/{portName}
```

- `productId` is 0 or 1. `portName` is the port, for example `air`, `status` or `security`.
- The four operations map to GET (read properties), PUT (write properties), POST (add) and DELETE.
- Every request carries `Connection: keep-alive` and, when cached credentials exist, the `Authorization` header.

### Request body

- GET is sent without a body.
- PUT and POST carry a JSON object (`application/json`). A PUT or POST with no data is refused by the app before anything is sent.
- DELETE carries a JSON body, `{}` when there is no data.
- Byte array values in the data are base64 encoded before the object is serialized.

### Response handling

| Status | Result |
|--------|--------|
| 200 | Success with the body. An empty body is treated as an "empty response" error |
| 400 | "Not understood" error |
| 401 | Cached credentials are cleared, new ones are computed from `WWW-Authenticate`, and the request is reported as unauthorized |
| 429 | "Busy" (too many requests) |
| 502 | "Cannot connect" |
| other | The body is searched for a known error string (`out of memory` maps to an out-of-memory error), otherwise a generic failure |

A response with no body object at all is a generic failure. A TLS handshake failure is reported as an insecure connection, any other I/O error as an I/O error.

### Retry on 401

Each get, put, add or delete is queued with a wrapper that catches the unauthorized result and queues the same request once more. Because the 401 handling has already stored fresh credentials, the second attempt is sent with the answer to the challenge. Any other error is passed to the caller unchanged.

### Serialization and availability

All LAN requests to one device go through a single serial request queue, so only one request is in flight at a time.

The LAN transport considers itself available when all of these hold:

- the device has a known IP address,
- a LAN network (Wi-Fi or Ethernet) is available,
- the current SSID equals the SSID stored for the device. If the current SSID is unknown, the app assumes it is the same network.

Availability is re-evaluated whenever connectivity, the Wi-Fi network or any field of the device record changes.

### Timeouts

The HTTP client uses 30 seconds for connect and read timeouts, plus write and call timeouts when those are enabled. Before each request the app also tries to reset the timeouts to a value of 30. In the decompiled code that value is in milliseconds, which is most likely a decompilation artifact or an app bug, with 30 seconds the probable intent.

## Encryption key exchange

The app does this in `GetKeyRequest`.

```
GET https://{ip}/di/v1/products/0/security

HTTP 200
{"key": "abcdef0123456789abcdef0123456789"}
```

- The port is always `security` and the product id is always 0.
- It is an ordinary LAN request, so it uses the same PhilipsCondor credentials.
- `key` is a 32-digit hex string, a 128-bit key. The app stores the hex string as `encryption_key`.
- A 200 reply without a non-empty `key` fails with "Key missing in response".

When it runs:

- Before every get, put, add or delete, if the device has no key and no key exchange is already running. The key request is put at the front of the request queue so it goes out before the request that triggered it.
- After any decryption failure: the stored key is cleared and a new exchange starts.
- Implicitly after a reboot, because discovery clears the key when the boot id changes (see "Device model").

## Payload encryption

The app uses AES for one thing only: the payload of UDP change events (`Crypto.decryptData`, whose single caller is `LocalSubscriptionHandler`). HTTP traffic is never encrypted at the payload level. Every LAN request goes to an `https://` URL with a plain JSON body, the response body is passed on as it arrives, and the `https` flag in the device database is always written as true and never read. Nothing in the app encrypts, so the 2 byte prefix below is added by the device.

### Parameters

| Item | Value |
|------|-------|
| Cipher | AES-128 in CBC mode |
| Padding | PKCS7 |
| Key | The 16 bytes of `encryption_key` |
| IV | 16 zero bytes |
| Prefix | 2 random bytes are prepended to the plaintext before encryption and discarded after decryption |
| Transport encoding | Base64 |

The app turns the hex key into bytes through a big-integer conversion and drops a leading sign byte if one appears. For a 32-digit key whose first byte is not zero this is the same as plain hex decoding.

### Decrypting an event payload

1. Strip leading and trailing characters with a code point at or below space.
2. Base64-decode.
3. AES-128-CBC decrypt with the key and a zero IV, remove PKCS7 padding.
4. Drop the first 2 bytes.
5. Decode the rest as a UTF-8 string.
6. Check that it parses as JSON.

If there is no key, the base64 decoding fails or the cipher reports an error, the result is a decryption failure, which triggers a new key exchange.

Illustration in Python (using the `cryptography` package):

```python
import base64
import json

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def decrypt_payload(body: str, key_hex: str) -> dict:
    raw = base64.b64decode(body.strip())
    key = bytes.fromhex(key_hex)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).decryptor()
    padded = decryptor.update(raw) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return json.loads(plain[2:].decode("utf-8"))
```

## TLS and certificate pinning

The relevant classes are `SSLContextFactory`, `SslPinTrustManager` and `PublicKeyPin`.

### Pin format

A pin is the SHA-256 digest of the leaf certificate's encoded public key (the DER `SubjectPublicKeyInfo`), base64 encoded. A stored pin must decode to exactly 32 bytes.

```python
import base64
import hashlib

from cryptography import x509
from cryptography.hazmat.primitives import serialization


def public_key_pin(cert_der: bytes) -> str:
    cert = x509.load_der_x509_certificate(cert_der)
    spki = cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(hashlib.sha256(spki).digest()).decode()
```

### Trust on first use

Only the first certificate of the chain (the leaf) is checked. Chain validation against a CA is not done.

- No pin stored: the presented pin is stored on the device record and the connection is accepted.
- Pin stored and equal: accepted.
- Pin stored and different: the presented pin is saved as `mismatched_pin` and the connection is rejected with "The appliance's certificate doesn't match the stored pin."

The app also has a TLS context that trusts every certificate, used for the initial discovery or probe when no pin is stored yet. All ongoing communication uses the pinned context.

### TLS settings

- TLS 1.2 only.
- Cipher suites limited to:
  - `TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256`
  - `TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256`
  - `TLS_RSA_WITH_AES_128_CBC_SHA256`
- Hostname verification is disabled, since devices are addressed by IP.

## LAN transport setup

`LanTransportContext` builds the HTTP clients and owns pairing and discovery.

- One HTTP client is cached per device, keyed by `cppid`. The whole cache is dropped whenever LAN availability changes.
- Each client is bound to a LAN-only network: Wi-Fi or Ethernet, never cellular, Bluetooth or VPN, and the network does not need validated internet access (devices on an isolated network are fine). The app waits at most 3 seconds for such a network, and if none appears the request fails as "network unavailable".
- Default connect timeout is 30 seconds. The network lookup timeout is 3 seconds.

## Discovery

The app discovers devices with SSDP and mDNS at the same time (`BaseLanDiscoveryStrategy`). There are three variants: both protocols (the default `LanDiscoveryStrategy`), mDNS only and SSDP only.

### Lifecycle

Discovery runs only while a caller has asked for it and the LAN is connected. Starting takes the Android multicast lock and starts both listeners; losing the LAN or a stop request stops both and releases the lock. A caller may pass a set of model ids to filter on, and an empty set means all models.

Discovery errors: 111 means the transport is unavailable, 112 means the connection is unavailable.

### From announcement to device record

Each announcement is turned into a device record with:

| Record field | Source |
|--------------|--------|
| boot id | Announced boot id, parsed as a number (-1 when it does not parse) |
| `cppid` | Announced cpp id |
| IP address | Sender address |
| name | Friendly name |
| model id | Model number |
| device type | Model name |
| SSID | The phone's current SSID |
| expiration | The announcement's expiration period |

Records that fail the validity check or the model filter are ignored. The rest go into a discovery cache keyed by `cppid`:

- A new device is added with its expiration period as time to live, and reported as discovered.
- A known device has its timer reset and is reported as discovered again.
- When a timer runs out, the device is removed from the cache and reported as lost.

### Discovered device records

LAN devices are found by SSDP or mDNS. A discovered device record holds: boot ID, CPP ID (the unique device ID, used as the cache key), device type, friendly name, IP address, manufacturer, model name (for example `AC2729`), model number, serial number, plus manufacturer URL, model URL, model description and presentation URL, and an expiration period in milliseconds.

Discovered devices are kept in a cache with a per-device TTL. Seeing the device again resets its timer; when the timer runs out the device is reported as lost. Discovery can be restricted to a set of model IDs. Discovery errors carry code 111 (transport unavailable) or 112 (connection error).

## UDP event subscriptions

Devices can push state changes over UDP (`LocalSubscriptionHandler`).

1. The app opens a UDP socket (one shared receiver for all devices) and notes the bound port.
2. It subscribes with `POST /di/v1/products/{id}/` and the body `{"subscriber": "<appId>", "ttl": 300, "changeudp": <port>}`. The default subscription time to live is 300 seconds.
3. The device acknowledges with HTTP 200 and the header `X-Condor-Features: changeindication-port`.
4. When its state changes, the device sends an encrypted packet to that UDP port.
5. The receiver hands each packet over with its port name and sender IP. Packets are ignored unless the sender IP equals the device's IP and the payload is non-empty.
6. The payload is decrypted as described under "Payload encryption". On success the JSON goes to the subscribers of that port. On failure the subscribers get a decryption failure event, and the LAN strategy clears the stored key and runs a new key exchange.

Disabling the subscription detaches the device from the shared receiver.

When subscribed on the LAN, devices push change indications to the phone over UDP. All LAN subscriptions share one receiving socket.

#### Receiving socket

- The app binds UDP port 8080 with address reuse enabled. If that bind fails, it binds any free port chosen by the OS and sends that port in `changeudp` instead.
- It holds an Android multicast lock (named `CondorMulticastLock`) while receiving.
- Each datagram is read into a 1024 byte buffer, so a payload longer than that is truncated.
- The socket is closed when the last subscriber goes away.

#### Packet format

A datagram is text in an HTTP-like layout:

```
NOTIFY /di/v1/products/{productId}/{portName} HTTP/1.1
...header lines...
<base64 AES-encrypted JSON payload>
```

The receiver trims the text, splits it on `\n`, and takes:

- the first line as the request line; the second space separated token is the path,
- the last line as the payload (base64 AES ciphertext, decrypted like a LAN response body),
- the sender IP address from the datagram itself, to tell devices apart.

The port identifier is built by splitting the path on `/` and joining every segment from index 4 on. Because the path starts with `/`, index 0 is empty and index 4 is the product ID, so `/di/v1/products/1/air` yields `1/air`, the same `{productId}/{portName}` form used as `path` on the cloud side. (An earlier reading claimed the result was just `air`; the smali starts the loop at index 4 of a split that keeps the leading empty segment, so the product ID is kept.)

A minimal parser of our own:

```python
def parse_notify(datagram: bytes) -> tuple[str, str]:
    lines = datagram.decode("utf-8", "replace").strip().split("\n")
    path = lines[0].split(" ")[1] # /di/v1/products/1/air
    port = "/".join(path.split("/")[4:]) # "1/air"
    return port, lines[-1].strip() # payload is base64 ciphertext
```

## Transport strategies

The app talks to an appliance through a "communication strategy". There is one for the LAN (`LanCommunicationStrategy`) and one for the HSDP cloud relay, combined by `CombinedCommunicationStrategy`, which picks the best available one. Each transport also provides its own discovery strategy (`LanTransportContext`, `HSDPTransportContext`).

Every strategy supports the same set of operations on a port, addressed by port name and product ID: get properties, put properties, add properties, delete properties, subscribe (with a TTL), unsubscribe, and execute a method with a parameter list. These map onto the Condor operations listed under "Message keys".

### Subscription payloads

Subscribe and unsubscribe requests carry a small JSON body:

- Unsubscribe: `{"subscriber": "<appId>"}`
- Subscribe: `{"subscriber": "<appId>", "ttl": <seconds>}`
- LAN subscribe adds the local UDP port the device should push to: `{"subscriber": "<appId>", "ttl": 300, "changeudp": <udpPort>}`

The subscription TTL used by the app is 300 seconds (5 minutes).

On the LAN, a successful subscribe reply may carry the header `X-Condor-Features`. If its value contains `changeindication-port`, the device supports UDP change indications on the port given in `changeudp`.

### App ID

The `subscriber` value is a per-install app ID: the literal prefix `deadbeef` followed by 8 hex digits from a random 32 bit integer, for example `deadbeef1a2b3c4d`. A client of our own can generate one the same way:

```python
import secrets
app_id = "deadbeef%08x" % secrets.randbits(32)
```

### Choosing between LAN and cloud

`CombinedCommunicationStrategy` keeps its sub-strategies in insertion order (LAN first, then cloud) and uses the first available one. When the availability of any of them changes, it waits a 1000 ms cool-down (restarting the wait on each new change) and then checks whether the best strategy is different. If it is, it unsubscribes every active subscription on the old transport and subscribes them again on the new one. Subscriptions are tracked by port name and product ID (the TTL is not part of their identity). Listeners for subscription events are registered on all sub-strategies, not only the active one.

When no transport is usable, a fallback strategy answers every call with NOT_CONNECTED.

On the LAN, the response body goes through AES decryption before being parsed as JSON; on the cloud path it is plain UTF-8 JSON.

### Request ordering

Requests run one at a time, in order, on a single background worker (`RequestQueue`). The only exception is the security key exchange, which is put at the front of the queue so it runs before anything already waiting.

## Error codes

The Condor SDK reports every failed request with one error value (enum `Error` in `connectivity/condor/core/request/`). Some carry a numeric code that matches the code a device returns in a Condor error reply. The others are client side conditions with no numeric code; the table notes what triggers them where the app makes it clear.

| Code | Name | Meaning |
|------|------|---------|
| 0 | NO_ERROR | No error |
| 1 | NOT_UNDERSTOOD | Request not understood |
| 2 | OUT_OF_MEMORY | Out of memory (during firmware upload this means "send a smaller chunk", see the firmware port) |
| 3 | NO_SUCH_PORT | No such port |
| 4 | NOT_IMPLEMENTED | Not implemented |
| 5 | VERSION_NOT_SUPPORTED | Version not supported |
| 6 | NO_SUCH_PROPERTY | No such property |
| 7 | NO_SUCH_OPERATION | No such operation |
| 8 | NO_SUCH_PRODUCT | No such product |
| 9 | PROPERTY_ALREADY_EXISTS | Property already exists |
| 10 | NO_SUCH_METHOD | No such method |
| 11 | WRONG_PARAMETERS | Wrong parameters |
| 12 | INVALID_PARAMETER | Invalid parameter |
| 13 | NOT_SUBSCRIBED | Not subscribed |
| 14 | PROTOCOL_VIOLATION | Protocol violation |
| 15 | TIMED_OUT | Request timed out |
| 255 | UNKNOWN | Unknown error |

Errors without a numeric code:

| Name | Trigger |
|------|---------|
| REQUEST_FAILED | Generic request failure |
| BUSY | HTTP 429 from the device |
| CANNOT_CONNECT | HTTP 502, cannot reach the appliance |
| UNRECOVERABLE_CONNECTION | Connection cannot be recovered |
| SEND_FAILED | MQTT publish failed, command not sent |
| IOEXCEPTION | I/O error |
| NO_REQUEST_DATA | Request attempted with null or empty data |
| NO_TRANSPORT_AVAILABLE | No transport available |
| NOT_CONNECTED | Not connected to an appliance (also what every call returns when no transport is active) |
| NOT_AVAILABLE | Communication not available |
| INSECURE_CONNECTION | TLS/SSL failure |
| REQUEST_UNAUTHORIZED | HTTP 401 |
| REJECTED | HSDP rejected the MQTT command |
| EMPTY_RESPONSE | HTTP 200 with an empty body |

## Polling and connectivity

### Poller

The SDK has a generic poller used by flows such as the firmware update. It calls get properties on a port at a fixed interval until an absolute deadline. Each tick waits for the reply for at most one interval, then the next tick starts. Successful replies are delivered as events; failed ones are ignored; reaching the deadline ends polling with a timeout. The firmware flows use a 1 second interval.

### Network availability

The SDK has two kinds of network monitor: a LAN one that is up while the phone is on a Wi-Fi or Ethernet network, and an internet one that is up while the phone has a network Android reports as validated (working internet access). The LAN side also tracks the current Wi-Fi SSID and notifies its listeners when it changes.

## HSDP cloud relay

The app's HSDP transport reaches appliances through Philips HealthSuite (HSDP) servers over MQTT instead of the LAN. The relevant classes live under `connectivity/condor/hsdp/` (`HSDPCommunicationStrategy`, `HSDPController`, `HSDPCommandQueue`, `HSDPTransportContext`).

### When the relay is used

The HSDP strategy reports itself available when the phone has internet connectivity and the appliance record has an `hsdpid`. It does not need the LAN `client_id` and `client_secret`; those are only used for local requests. The transport uses an internet-capable connectivity check, not a LAN-only one.

Connection state is one of `DISCONNECTED`, `CONNECTED` or `UNKNOWN`.

### Operation mapping

Every port operation becomes a Condor control message sent to the appliance's `hsdpid`:

| Port operation | Condor `op` |
|---|---|
| read properties | `GetProps` |
| write properties | `PutProps` |

The full set of operation names known to the app is:

| `op` value | Meaning |
|---|---|
| `AddProps` | add properties |
| `ChangeIndication` | push event from a subscription |
| `DelProps` | delete properties |
| `ExecMethod` | remote method call (used by the pairing port) |
| `GetPorts` | list ports |
| `GetProds` | list products |
| `GetProps` | read a port |
| `PutProps` | write a port |
| `Subscribe` | subscribe to a port |
| `Unsubscribe` | cancel a subscription |
| `Unknown` | fallback value |

### Condor control message

The payload carried inside the HSDP command is a JSON object:

| Key | Type | Content |
|---|---|---|
| `condorVersion` | string | always `"1"` |
| `op` | string | operation name from the table above |
| `path` | string | `"{productId}/{portName}"`, for example `"1/air"` |
| `values` | object | request data; any binary values are base64 encoded (no line wrapping) |
| `ttl` | number | only present when a subscription TTL is set |
| `status` | number | in responses: 0 means success, anything else is an error code |

Example read request:

```json
{
    "condorVersion": "1",
    "op": "GetProps",
    "path": "1/air",
    "values": {}
}
```

The original protocol summary showed this message with `"ttl": 30`. Per the request builder, `ttl` inside the Condor message is only added for subscriptions; the 30 second TTL that every request carries belongs to the outer HSDP command (see below).

### Request and response sequence

`HSDPRemoteRequest` turns one Condor message into a blocking request/response exchange:

1. Generate a UUID as the command identifier. It is sent as the command name and is used to match the reply.
2. Publish the command to the appliance's `hsdpid` with "require acknowledgement" set and a time to live of 30 seconds.
3. Wait up to 30 seconds (30000 ms) for the publish to be acknowledged. If it is not, or the publish reports an error, the request fails with `SEND_FAILED`.
4. Wait up to 30 seconds for the appliance's reply. Incoming messages are classified by their type:
   - `accepted`: the HSDP service accepted the command. This is not the appliance's answer and is ignored.
   - `rejected`: the command was refused. The request ends with `REJECTED`.
   - `notification`: the appliance's answer. It is used only if the command name in it equals the request's UUID. Its status detail, as a JSON string, is the response.
5. If no matching notification arrived in time, the request fails with `REQUEST_FAILED`.
6. If the response has a non-zero `status`, the request fails with the error mapped from that code (see "Request errors" below).
7. Otherwise the `values` field, serialized back to JSON, is the result.

### Serialization and reconnects

`HSDPCommandQueue` sends commands strictly one at a time: the next command is only published after the previous one's publish completion has been reported. Connect, disconnect and listener registration are passed straight through.

When the HSDP access tokens are refreshed, `HSDPController` disconnects the MQTT session and connects again with the new tokens. A failed refresh is reported as a connection error.

### Cloud pairing port

The `pairing` port (product ID 0) is the cloud pairing interface, handled by `DeviceCloudPairingPort`. It is not read or written; instead the app invokes remote methods on it through `ExecMethod`:

| Method | Parameters |
|---|---|
| `Pair` | three strings, or an extended form with five strings and a string array |
| `Unpair` | four strings |

The meaning of the individual parameters was not recovered from the app.

The result is a list whose first element is a number. The app maps it to its own result code:

| Outcome | App result |
|---|---|
| first element is `0` or `1` | 0 (success) |
| any other value | -1 (error) |
| the request itself failed | -3 |

#### Controller connection

The HSDP controller (`HSDPController`) connects in three steps: sign on to HSDP IAM, look up the service tagged `IOT` in HSDP service discovery to get the `wss://` MQTT endpoint and topic prefix, then connect the MQTT control service with the access token and a signed token. A command sent while disconnected triggers a connect first. When the token is refreshed, the controller reconnects.

#### Change indications over MQTT

Push updates on the cloud path arrive as MQTT messages of type `notification` (compared case-insensitively). The message's command carries a `statusDetail` object; when its `op` is `ChangeIndication`, the object is read as a Condor message, and its `values` (re-encoded as JSON) are delivered as an update for the port named in `path`.

#### Error values

Connection level errors: AUTHENTICATION_FAILED (wraps one of the authentication errors below), INTERRUPTED, NO_CONTROL_SERVICE_AVAILABLE (no control service found through discovery), MISSING_ACCESS_TOKEN, MISSING_SIGNED_TOKEN, CONNECT_FAILED (MQTT connect failed), SEND_FAILED (MQTT publish failed).

Authentication level errors:

| Name | Meaning |
|------|---------|
| MISSING_BOOTSTRAP_IDENTITY | No bootstrap credentials |
| BOOTSTRAP_SIGN_ON_FAILED | Bootstrap sign on failed |
| SERVICE_DISCOVERY_FAILED | HSDP service discovery failed |
| MISSING_PROVISIONING_URL | Provisioning service URL not found |
| MISSING_PROVISIONING_EVIDENCE | No provisioning evidence |
| PROVISIONING_FAILED | Provisioning API call failed |
| INSUFFICIENT_IDENTITY_INFORMATION | Required identity fields missing |
| PROVISIONED_SIGN_ON_FAILED | Token request with the provisioned identity failed |
| MISSING_TOKEN_RESPONSE | Token response was empty |
| TOKEN_EXPIRY_TOO_SHORT | Token expires in under 30 seconds |

## Message keys

Condor messages on the cloud path are JSON objects with these keys:

| Key | Content |
|-----|---------|
| `condorVersion` | Protocol version, always `"1"` |
| `op` | Operation name |
| `path` | Target, as `{productId}/{portName}` |
| `values` | Request or response data |
| `ttl` | Subscription TTL in seconds |

Operation names sent in `op` (checked against the `CondorOperation` smali):

| `op` value | Meaning |
|------------|---------|
| `GetProps` | Read a port (HTTP GET equivalent) |
| `PutProps` | Write port properties (HTTP PUT equivalent) |
| `AddProps` | Add properties (HTTP POST equivalent) |
| `DelProps` | Delete properties (HTTP DELETE equivalent) |
| `ExecMethod` | Call a method on a port |
| `GetPorts` | List a product's ports |
| `GetProds` | List products |
| `Subscribe` | Subscribe to change indications |
| `Unsubscribe` | Cancel a subscription |
| `ChangeIndication` | Push from the device that a port changed |
| `Unknown` | Unrecognised operation |

## HSDP authentication

`HSDPAuthentication` obtains the tokens used for the HSDP MQTT session. It talks to an HSDP IAM service and a service discovery service, whose base paths come from the HSDP configuration.

### Configuration data

| Item | Fields | Purpose |
|---|---|---|
| bootstrap credentials | client ID, client secret | app-level credentials used once to provision an identity |
| provisioned identity | client ID, client secret, username, password, HSDP identifier, identity signature | per-installation identity, persisted after provisioning |
| token set | access token, refresh token, signed token, access token lifetime | cached tokens, persisted |
| provisioning evidence | key/value map | data sent when provisioning for the first time |

The configuration also exposes operations to persist the identity and token set and to refresh the access tokens.

### Sign-on flow

1. If a token set is cached, it is installed (access, refresh and signed token) with a passive refresh policy, then service discovery runs. Done.
2. Otherwise, if no identity has been provisioned yet:
   1. Bootstrap sign-on: request an access token from IAM with the bootstrap client ID and secret.
   2. Run service discovery.
   3. Provision an identity.
   4. Start the sign-on flow again, which now takes step 3.
   Any failure along the way ends the flow with that error.
3. Otherwise (identity present, no token set): provisioned sign-on.
   - If a previously fetched token response still has more than 120 seconds of validity left, it is reused.
   - Otherwise request tokens from IAM with the identity's client ID, client secret, username and password, compute the absolute expiry as now plus `expires_in`, persist the tokens and run service discovery.

### Token expiry

Two constants govern expiry: a 120 second cache threshold (used above) and a 30 second invalidation window. When a token lifetime is received, the app checks that the lifetime minus 30 seconds is not negative (otherwise it reports an error), then arms a timer for the lifetime. When the timer fires, it runs a provisioned sign-on again if no token set is available, or refreshes the access tokens with the refresh token if one is.

## DaConnect authentication

`DaAuthenticationService` (under `cl/daconnect/authentication/`) wraps the credential providers and moves between the OIDC (Gigya) identity and the HSDP identity. For FUSION devices this is a two stage token exchange:

1. The user signs in through Gigya and the app receives an OIDC `id_token` and `access_token`.
2. The OIDC tokens are exchanged for HSDP tokens through the SAS API:

```http
POST https://sas.eu-da.iot.versuni.com/api/user/self/hsdp-token
Authorization: Bearer {OIDC access_token}
Accept: application/vnd.oneka.v2.0+json
Content-Type: application/vnd.oneka.v2.0+json

{"idToken": "{OIDC id_token}", "exchangeFor": "HSDP"}
```

3. The response (`SasHsdpTokensResponse`) contains `accessToken`, `refreshToken` and `signedToken`. The `accessToken` is an HSDP IAM token. The `signedToken` is the cryptographic proof required by the AWS IoT custom authorizer.

### Models

The DaConnect SDK (`cl/daconnect/authentication/`) supplies tokens and MQTT connection data to the rest of the app.

- It exposes an access token and an ID token (Gigya/HSDP), plus the user's federated user ID.
- The MQTT connection info has four fields: an HSDP access token, an MQTT signature used by the AWS IoT Custom Authorizer, an HSDP tenant identifier, and the `wss://` WebSocket URL. The app can invalidate this info to force fresh credentials.
- User authentication state is one of SIGNED_IN, SIGNED_OUT, SIGNED_OUT_FEDERATED_TOKEN_INVALID or UNKNOWN.

## MQTT connection info

FUSION devices are controlled over MQTT on AWS IoT Core, authenticated by a custom authorizer. The app keeps the connection parameters in `MqttConnectionInfo`; the client is `DaMqttClientImpl` (smali only, `com/philips/cl/daconnect/device_control/mqtt/`).

| Field | Source |
|---|---|
| access token | HSDP access token from the SAS exchange |
| MQTT signature | returned by the `/user/self/signature` endpoint |
| tenant | for example `da` |
| WebSocket URL | the `wss://` endpoint |

The WebSocket handshake to the `wss://` endpoint must carry these headers:

| Header | Value |
|---|---|
| `x-amz-customauthorizer-name` | always `CustomAuthorizer` |
| `x-amz-customauthorizer-signature` | the MQTT signature from the signature endpoint |
| `token-header` | `Bearer {accessToken}` (the HSDP access token) |

Although `token-header` carries the access token, the `signedToken` from the SAS exchange is passed alongside the access token when the HSDP control service connects. On some FUSION firmware versions the connection is terminated if the signed token is missing, or if the token used lacks the correct `aud` claim, which the SAS exchange provides.

## Credential storage

The app keeps one row per appliance in a SQLite database named `network_node`, table `network_node`, managed by `NetworkNodeDatabase`. The schema is at version 9. All values, including `encryption_key` and `client_secret`, are stored in plain text.

### Columns

| Column | Type | Meaning |
|---|---|---|
| `_id` | INTEGER NOT NULL UNIQUE | row ID, primary key |
| `cppid` | TEXT UNIQUE | unique device identifier; appliances are identified by it |
| `mac_address` | TEXT | MAC address (added in v7) |
| `bootid` | NUMERIC | device boot counter |
| `encryption_key` | TEXT | AES-128 key as a hex string, from the `security` port |
| `dev_name` | TEXT | friendly name |
| `lastknown_network` | TEXT | Wi-Fi SSID the device was last seen on |
| `is_paired` | SMALLINT NOT NULL DEFAULT 0 | pairing state ordinal, see below |
| `last_paired` | NUMERIC | timestamp of the last pairing |
| `ip_address` | TEXT | LAN IP address |
| `device_type` | TEXT | model name |
| `model_id` | TEXT | model number |
| `https` | SMALLINT NOT NULL DEFAULT 0 | HTTPS flag; the app always writes true |
| `pin` | TEXT | TLS certificate pin: SHA-256 of the TLS public key, base64 (added in v4) |
| `mismatched_pin` | TEXT | new pin seen when the certificate changes (added in v6) |
| `client_id` | TEXT | locally generated base64 of 16 random bytes (added in v8) |
| `client_secret` | TEXT | secret issued by the device during pairing (added in v8) |
| `hsdpid` | TEXT | HSDP cloud device ID (added in v9) |

Pairing state values in `is_paired`:

| Value | State |
|---|---|
| 0 | PAIRED |
| 1 | NOT_PAIRED |
| 2 | UNPAIRED |
| 3 | PAIRING |

### Schema history

| Version | Change |
|---|---|
| 1 | initial: `cppid`, `bootid`, `encryption_key`, `dev_name`, `lastknown_network`, `is_paired`, `last_paired`, `ip_address`, `model_name`, `model_type` |
| 2 | added `https` |
| 3 | renamed `model_type` to `model_id` |
| 4 | added `pin` |
| 5 | renamed `model_name` to `device_type` |
| 6 | added `mismatched_pin` |
| 7 | added `mac_address`, defaulting to the `cppid` value |
| 8 | added `client_id` and `client_secret` |
| 9 | added `hsdpid` |

### When rows are written

At startup the app loads every row and turns it into an appliance. From then on, any change to an appliance's network node properties writes the row back. Adding an appliance inserts a row; forgetting it deletes the row (and the app's separate appliance data).

## Crypto helpers

The byte helpers in `ByteUtil` (`connectivity/condor/core/security/`) define a few conventions used by pairing and payload decryption:

- `client_id` is 16 bytes (128 bits) from a secure random generator, base64 encoded.
- Hashing is SHA-256.
- Base64 encoding uses no line wrapping. Decoding accepts default base64.
- After AES decryption the first 2 bytes are dropped, unless the data is shorter than 3 bytes, in which case it is returned unchanged. A matching helper that prepends 2 random bytes before encryption exists but is never called.
- Hex strings are converted two characters per byte; bytes are rendered as uppercase hex.
- When a property map contains binary values, they are replaced by their base64 string before sending (this is how HSDP `values` are built).
- A separate random number helper uses a length of 224 bits.

## Protocol summary

### LAN requests

| Step | URL | Method | Auth | Body |
|---|---|---|---|---|
| Probe | `https://{ip}/di/v1/products/{0,1}/` | GET | none | none |
| Pair | `https://{ip}/auth/v1/` | PUT | none | `{"id": "<client_id>"}` |
| Pair (challenge) | `https://{ip}/auth/v1/` | PUT | none | `{"id": "<client_id>", "key": "<SHA256(seed+id)>"}` |
| Get key | `https://{ip}/di/v1/products/0/security` | GET | PhilipsCondor | none |
| Get state | `https://{ip}/di/v1/products/{id}/{port}` | GET | PhilipsCondor | none |
| Set state | `https://{ip}/di/v1/products/{id}/{port}` | PUT | PhilipsCondor | JSON properties |
| Subscribe | `https://{ip}/di/v1/products/{id}/{port}` | POST | PhilipsCondor | `{"subscriber":"...","ttl":300,"changeudp":<port>}` |
| Unsubscribe | `https://{ip}/di/v1/products/{id}/{port}` | DELETE | PhilipsCondor | unsubscription data |

### PhilipsCondor header

The header value is the scheme name followed by base64 of the client ID bytes concatenated with a SHA-256 digest:

```
Authorization: PHILIPS-Condor base64(clientId + SHA256(challenge + clientId + clientSecret))
```

A short illustration:

```python
import base64
import hashlib

def condor_header(challenge: bytes, client_id: bytes, client_secret: bytes) -> str:
    digest = hashlib.sha256(challenge + client_id + client_secret).digest()
    return "PHILIPS-Condor " + base64.b64encode(client_id + digest).decode()
```

### Payload decryption

| Parameter | Value |
|---|---|
| Algorithm | AES-128-CBC with PKCS#7 padding |
| Key | `encryption_key`, hex decoded to 16 bytes |
| IV | 16 zero bytes |
| Steps | base64 decode, AES decrypt, drop the first 2 bytes, decode as UTF-8 |

```python
import base64

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

def decrypt_payload(b64: str, key_hex: str) -> str:
    raw = base64.b64decode(b64)
    dec = Cipher(algorithms.AES(bytes.fromhex(key_hex)), modes.CBC(bytes(16))).decryptor()
    padded = dec.update(raw) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return plain[2:].decode("utf-8")
```

### HSDP messages

Condor messages over HSDP MQTT use the JSON shape described in "Condor control message" above. Reply types are `accepted` (acknowledged by the service), `rejected` (error) and `notification` (the appliance's data).

### Key storage

Credentials live in the `network_node` SQLite table described under "Credential storage". The fields that matter for talking to a device are `client_id`, `client_secret`, `encryption_key`, `hsdpid` and `pin`.

## Appliances and ports

### Appliance model

An appliance (`Appliance` in `connectivity/condor/core/appliance/`) is a network node (identity and credentials) plus a communication strategy and a set of ports. When several strategies are available (LAN and HSDP) they are combined into one. Every appliance automatically gets the `device` port. An appliance is available when its strategy is, and two appliances are the same if their `cppid` matches. Each appliance subclass names its device type.

### Discovery and lifecycle

`ApplianceManager` tracks two sets keyed by `cppid`: appliances ever seen and appliances currently visible. When discovery or the database yields a node:

1. If it is already visible, its record is updated.
2. If it is known but not visible, it is marked visible and reported as found.
3. Otherwise the app's appliance factory decides whether it can create an appliance for the node; if so the new appliance is added to both sets and reported as found. Nodes the factory does not recognise are ignored.

`CondorEntryPoint` is the single SDK instance that owns the manager, collects the discovery strategies of each transport, and starts or stops discovery, optionally limited to a set of model IDs.

### Port behaviour

Every port (`CondorPort` in `connectivity/condor/core/port/`) is identified by a port name such as `air`, `security` or `pairing`, and a product ID of 0 or 1. Its behaviour:

- Only one request per port is in flight at a time. Pending work is served in this priority order: property writes, subscribe, unsubscribe, property reads, method calls.
- Responses are merged into the cached properties key by key, not replaced. A partial update (for example from a subscription event) only changes the keys it contains.
- A port is marked as "applying changes" from the start of a write until the last queued write has completed.
- Subscriptions use the strategy's subscription TTL and are renewed automatically on a timer of that many seconds.
- Incoming subscription events are decrypted and merged like responses. If an event cannot be decrypted, the port falls back to a full read.

## Port reference

On the LAN, a port is addressed as `/di/v1/products/{productId}/{portName}`. On the cloud path the same port is the `path` value `{productId}/{portName}`.

| Port name | Product ID | App class | Purpose |
|-----------|-----------|-----------|---------|
| `device` | 1 | `DevicePort` | Device info: name, model, firmware version |
| `security` | 0 | `SecurityPort` | Encryption key exchange |
| `pairing` | 0 | `DeviceCloudPairingPort` | Cloud pairing via HSDP |
| `pairing` | 0 | `HsdpPairingPort` | HSDP pairing (simplified API, same port) |
| `firmware` | 0 | `FirmwarePort` | Firmware version info and update |
| `wifi` | 0 | `WifiPort` | Wi-Fi configuration |
| `wifinetworks` | 0 | `WifiNetworksPort` | Wi-Fi scan results |
| `wifiui` | 0 | `WifiUiPort` | Wi-Fi setup UI state |
| `time` | 0 | `TimePort` | Device time |
| `locale` | 0 | `LocalePort` | Locale setting |
| `log` | 0 | `LogPort` | Device logs |
| `logsettings` | 0 | `LogSettingsPort` | Log configuration |
| `backend` | 0 | `BackendPort` | Backend connectivity settings |
| `transport` | 0 | `TransportPort` | Transport layer status |
| `fac` | 0 | `FacPort` | Factory settings |
| `bleparams` | 0 | `BleParamsPort` | Bluetooth parameters |
| `*.b` | varies | `BinaryCondorPort` | Raw binary data, not JSON |

### device port

Read with GET on `/di/v1/products/1/device`. Renaming the device is a PUT of `{"name": "<new name>"}`.

| Key | Content |
|-----|---------|
| `name` | Friendly name |
| `type` | Device type |
| `modelid` | Model ID |
| `modelname` | Model name |
| `productname` | Product name |
| `productversion` | Product version |
| `serial` | Serial number |
| `ctn` | Commercial type number |
| `udi` | Unique device identifier |
| `swversion` | Software version |
| `wificountry` | Wi-Fi country code |
| `allowuploads` | Whether firmware uploads are allowed |

The app also models an "allow pairing" flag on this port; its JSON key is not established.

### pairing port (HSDP)

Port `pairing`, product 0. Properties:

| Key | Content |
|-----|---------|
| `hsdpid` | The appliance's HSDP ID |
| `previousaction` | `Pair` or `Unpair` |
| `previousresult` | `true` if the last operation succeeded |
| `previoustrustee` | Trustee of the last operation |
| `previoustype` | Type of the last operation, for example `control` |

The app also reads a `semantics` field. The pairing flow itself is described below.

### Binary ports

A port whose name ends in `.b` returns raw bytes instead of JSON. The app stores the body as is and does not support method calls on such ports.

### Other ports

What the app models for the remaining ports:

| Port | Fields |
|------|--------|
| `time` | `utc` (integer), `zone` (string) |
| `locale` | `locale` (string) |
| `wifi` | `ssid`, `password`, `security`, `ip`, `gateway`, `netmask`, `dhcp` |
| `wifinetworks` | Scan results, see below |
| `log` | Device log entries |
| `logsettings` | Log level configuration |
| `backend` | Backend server configuration (many fields) |
| `bleparams` | BLE advertising and connection parameters |
| `transport` | Transport status |
| `wifiui` | Wi-Fi setup UI state |
| `fac` | Factory settings |

The `wifinetworks` port returns an object keyed by SSID, each value describing one network:

```json
{"MyNetwork": {"channel": "6", "quality": 80, "security": "WPA2"}}
```

Each entry has `channel` (string), `quality` (integer signal quality), `security` (for example `WPA2` or `OPEN`) and `ssid`.

### Number handling

The app parses port JSON so that numbers without a decimal point stay integers and numbers with one become floats, so a value such as `42` is kept as an integer. Python's `json.loads` behaves the same way.

### security port

`GET /di/v1/products/0/security` returns the payload encryption key:

| Key | Meaning |
|---|---|
| `key` | current encryption key, hex string |
| `nextkey` | next key, for key rotation; may be null |

```json
{"key": "abcdef0123456789...", "nextkey": null}
```

## HSDP pairing flow

Cloud control requires the appliance to be paired with the app's HSDP identity through the `pairing` port. The app does this with `HsdpPairingHandler`, called from `HSDPControlPairingHandlerImpl` with type `control` and the app's HSDP identifier as trustee.

1. Subscribe to the `pairing` port so change indications are received. If the subscribe fails, the flow ends with that error.
2. Call the method `Pair` (or `Unpair`) on the port with the parameter list `[type, trustee]`, for example `["control", "<hsdp identifier>"]`.
3. The method returns a list. If its first value is `0`, the device has accepted the request; any other value means it was rejected (REJECTED).
4. After acceptance, start a 30 second watchdog.
5. Wait for a change indication on the `pairing` port whose `previoustype` and `previoustrustee` equal the values sent. If `previousresult` is `true` the operation succeeded, otherwise it failed (REJECTED). Change indications for other type/trustee pairs are ignored.
6. If no matching change indication arrives within 30 seconds, the flow fails with TIMED_OUT.
7. After a successful `Pair`, the app reads `hsdpid` from the pairing port and stores it as the appliance's HSDP ID. After a successful `Unpair`, it clears the stored HSDP ID.

## Firmware port

Port `firmware`, product 0.

### Properties

| Key | Content |
|-----|---------|
| `state` | Update state, see below |
| `version` | Current firmware version |
| `upgrade` | Version available for upgrade; a non-empty value means an update is available |
| `versions` | Map of component versions |
| `name` | Firmware file name |
| `size` | Image size in bytes |
| `progress` | Bytes received so far (not a percentage) |
| `maxchunksize` | Largest chunk the device accepts per write |
| `mandatory` | Whether the update is mandatory |
| `candownload` | Whether the device can download |
| `canupgrade` | Whether the device can upgrade |
| `statusmsg` | Status message |
| `data` | Base64 firmware chunk (written by the client) |

### States

| Value read | Value written | State |
|------------|---------------|-------|
| `idle` | `idle` | Idle |
| `preparing` | | Preparing |
| `downloading` | `downloading` | Receiving the image |
| `checking` | | Verifying the image |
| `ready` | | Image verified, ready to install |
| `programming` or `go` | `go` | Installing |
| `canceling`, `cancelling` or `cancel` | `cancel` | Cancelling |
| `error` | | Error |
| `unknown` | | Unknown |

### Local upload flow

The app's local update (`FirmwareUpdatePushLocal` with `FirmwareUploader`) runs as follows. Polling uses a 1 second interval.

1. Read the firmware port to get the current state.
2. If the state is `idle`, PUT `{"state": "downloading", "size": <image size>}` and poll until the state reads `downloading`, with a 10 second timeout. Then start uploading at offset 0.
3. If the state is already `downloading` and the app is resuming, continue uploading at the offset given by `progress`.
4. In any other state, PUT `{"state": "cancel"}`, then `{"state": "idle"}`, and start again from step 1.
5. Upload loop: take `maxchunksize` from the last port read (a value of 0 or less is an error). Send the bytes from the current offset as a PUT of `{"data": "<base64 chunk>"}`. On success read `progress` from the port's latest properties; if it has reached the image size go to step 6, otherwise send the next chunk from `progress`. If the device answers OUT_OF_MEMORY (code 2), shrink the chunk by 50 bytes and resend from the same offset (each further OUT_OF_MEMORY takes off another 50). Any other error aborts the upload.
6. Wait for verification: if the state is already `ready`, the upload is done; otherwise subscribe and poll until it reads `ready` (success) or `error` (failure).
7. Install: PUT `{"state": "go"}` and poll until the state returns to `idle`, which means the device rebooted with the new image. `error` means the install failed, and so does reaching the caller's timeout.
8. Cancel (at any point): stop uploading and polling, PUT `{"state": "cancel"}`, and poll until the state reads `error`, which is how the device acknowledges a cancel. A timeout here means the cancel failed.

The app also has a variant that sends the image as one raw binary PUT (`application/octet-stream`, with the device credentials in the Authorization header) instead of base64 JSON chunks. Its target URL cannot be recovered from the APK: it is built from scheme `https`, the device IP as host and `firmwaredata` as the file part with no leading slash, which turns into `https://{ip}firmwaredata`. When the HTTP client parses that string again, the host becomes `{ip}firmwaredata` and the path `/`. Nothing in the APK shows that a `/firmwaredata` path exists on the device.

## End-to-end flows

This section follows complete operations from start to finish, in the order the app performs them. App class names are given only as pointers for further APK lookups.

### C.1 Device lifecycle (discovery to polling)

#### Startup

1. The app's connectivity entry point (`CondorEntryPoint`) creates an appliance manager with all discovery strategies.
2. Every known device (network node) is loaded from the app's local SQLite database.
3. An appliance object is created for each loaded node. Each appliance gets a combined transport that holds the LAN transport first and the HSDP cloud transport second (see C.6).
4. Any change to a node's properties is written back to the database automatically.

#### Discovery

1. LAN discovery acquires the Android multicast lock, then starts an SSDP control point and an mDNS browser at the same time.
2. When a device answers on either, a node is built from what was discovered: cppId, IP address, name, model, SSID and bootId.
3. The node is dropped if its model id does not pass the configured model filter.
4. The node is put in a discovery cache with a time to live (see Cache expiration).
5. The appliance manager then handles the node:
   - already discovered: the existing appliance is updated and an "updated" event is raised;
   - known from the database but not yet seen on the network: moved to the discovered set and a "found" event is raised;
   - new: an appliance is created, stored under its cppId in both the known and discovered sets, and a "found" event is raised.

#### Cache expiration

If a device is not rediscovered within the cache TTL (15 seconds by default), it is removed from the discovery cache. If it is not persisted in the database it is also removed from the discovered set, and a "lost" event is raised.

### C.2 LAN request execution (single GET)

This is the path of one property read, for example `GET` on the `air` port of product 1.

#### Request serialization per port

Each port object allows only one request in flight. When the current request completes, the next queued one starts. When several kinds of operation are pending, they are picked in this order:

1. put
2. subscribe
3. unsubscribe
4. get
5. execMethod

#### Transport selection

The combined transport walks its transports in insertion order (LAN, then HSDP) and uses the first one that reports itself available. If none is available, every operation fails with `NOT_CONNECTED`.

#### LAN steps

1. If the node has no encryption key and no key exchange is running, a key exchange is queued at the front of the request queue: `GET /di/v1/products/0/security`, answered with `{"key": "<hex>", "nextkey": null}` (see C.4).
2. The request is wrapped so that a `REQUEST_UNAUTHORIZED` result causes one automatic retry with fresh credentials (see the 401 row below).
3. The request is built:
   - URL `https://{ip}/di/v1/products/{productId}/{port}`, e.g. `https://{ip}/di/v1/products/1/air`
   - header `Connection: keep-alive`
   - header `Authorization` if credentials for this node are cached (see C.3)
   - no body for `GET`, a JSON body for `PUT`, `POST` and `DELETE`
4. The HTTPS client is cached per cppId. A new one is created on a cache miss with these settings:
   - bound to a LAN-only network (Wi-Fi or Ethernet), waiting up to 3 s for one
   - TLS 1.2
   - trust on first use certificate pinning (the device certificate is pinned the first time it is seen)
   - hostname verification disabled, since devices are reached by IP
   - cipher suites (as named in the earlier analysis): ECDHE_RSA_AES128_GCM, ECDHE_ECDSA_AES128_GCM, RSA_AES128_CBC_SHA256
5. The app calls a helper that sets the client timeouts to the value 30 with the unit milliseconds. The earlier analysis noted this is literally what the decompiled code says and is probably a decompilation artifact or an app bug, with 30 seconds being the likely intent.
6. The request is executed and the response is mapped:

| Result | Meaning in the app |
|---|---|
| 200 with a body | success, body returned |
| 200 with an empty body | `EMPTY_RESPONSE` error |
| 400 | `NOT_UNDERSTOOD` |
| 401 | cached credentials are cleared, new credentials are computed from the `WWW-Authenticate` challenge and stored, result is `REQUEST_UNAUTHORIZED`, then the wrapper from step 2 retries with the new credentials |
| 429 | `BUSY` |
| 502 | `CANNOT_CONNECT` |
| any other status | an error code taken from the response body if one is present, else `REQUEST_FAILED` |
| TLS handshake failure | `INSECURE_CONNECTION` |
| other I/O failure | `IOEXCEPTION` |

#### Processing the response

1. The body bytes are turned into a JSON string. For HTTP responses this is a plain UTF-8 decode (see the correction in C.12j).
2. The incoming JSON is merged into the port's cached properties rather than replacing them (algorithm in C.12d).
3. The merged result becomes the new cache, all waiting get callbacks receive it (C.12g), the port is marked idle and the next queued request starts.

### C.3 PhilipsCondor challenge-response

The device protects its HTTPS API with a custom challenge scheme. Inputs are the `clientId` and `clientSecret` obtained during LAN pairing (C.11), both stored base64 encoded.

1. The client sends a request without credentials and receives:

   ```
   HTTP/1.1 401
   WWW-Authenticate: PHILIPS-Condor <base64 challenge>
   ```

2. Strip the `PHILIPS-Condor` scheme name (matched case-insensitively) and base64 decode the rest. The result must be exactly 16 bytes.
3. Both `clientId` and `clientSecret` must be present and non-empty.
4. Base64 decode `clientId` and `clientSecret` (16 bytes each).
5. Compute `hash = SHA-256(challenge || clientId || clientSecret)`, over 48 bytes, giving 32 bytes.
6. Build `response = clientId || hash` (48 bytes).
7. Retry the same request with `Authorization: PHILIPS-Condor <base64(response)>`. The device answers 200 if the credentials are valid.

The computed header is cached for the node and reused on later requests. It is discarded on the next 401, which starts the cycle again.

```python
import base64
import hashlib


def condor_authorization(www_authenticate: str, client_id_b64: str,
                         client_secret_b64: str) -> str:
    value = www_authenticate.strip()
    if value.lower().startswith("philips-condor"):
        value = value[len("philips-condor"):].strip()
    challenge = base64.b64decode(value)
    if len(challenge) != 16:
        raise ValueError("challenge must be 16 bytes")
    cid = base64.b64decode(client_id_b64)
    secret = base64.b64decode(client_secret_b64)
    digest = hashlib.sha256(challenge + cid + secret).digest()
    return "PHILIPS-Condor " + base64.b64encode(cid + digest).decode()
```

### C.4 AES decryption pipeline (UDP events)

UDP change events carry base64 encoded AES ciphertext instead of JSON. The earlier analysis first placed this decryption in the HTTP response path, for devices marked `https=0`. C.12j corrects that: the app only runs it on UDP subscription events, and it writes `https` as true for every device. The decryption itself is as follows.

1. Empty input gives no result.
2. If the node has no encryption key, decryption fails and a key re-exchange is triggered (step 9).
3. Strip whitespace: every character whose code is less than or equal to that of a space is removed.
4. Base64 decode the remaining text to get the ciphertext.
5. Key: the hex key string from the security port is parsed as a big-endian integer and converted to bytes. If that byte array starts with a 0x00 sign byte, the 16 bytes after it are the key; otherwise the first 16 bytes are the key.
6. Decrypt with AES-CBC, PKCS#7 padding, IV of 16 zero bytes.
7. Drop the first 2 bytes of the plaintext (a random prefix the device adds before encrypting).
8. Decode the rest as text and check that it parses as a JSON object. If it does not, the result is discarded.
9. On any crypto or decoding error, the app clears the stored key and runs a new key exchange: `GET /di/v1/products/0/security`, response `{"key": "<hex>", "nextkey": null}`, and stores the new hex key.

```python
import base64

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def decrypt_payload(data: str, hex_key: str) -> bytes:
    text = "".join(c for c in data if c > " ")
    ciphertext = base64.b64decode(text)
    key = bytes.fromhex(hex_key)[:16]
    dec = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).decryptor()
    padded = dec.update(ciphertext) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return plain[2:]
```

The Python key handling above takes the first 16 bytes of the hex string. That matches the app's sign-byte rule for a 32 hex digit key whose first byte is not zero.

### C.5 LAN subscription lifecycle

LAN subscriptions make the device push property changes to the app over UDP.

#### Subscribe

1. The port is marked subscribed and a resubscribe timer is scheduled for TTL seconds.
2. A UDP receiver is started if it is not running yet. It binds a datagram socket to port 8080, or to a random port if 8080 cannot be bound, and reports the bound port. If no port could be bound, the subscribe fails with `NO_TRANSPORT_AVAILABLE` ("Could not register UDP port"); if the socket cannot be created at all, it fails with `IOEXCEPTION` (see C.12e).
3. The encryption key is exchanged if needed (C.4).
4. Subscribe request:

   ```
   POST /di/v1/products/{productId}/{port}

   {"subscriber": "deadbeef<random>", "ttl": 300, "changeudp": <udp port>}
   ```

   The subscriber id starts with the fixed prefix `deadbeef` followed by a random part. Like other requests, a 401 triggers one retry with new credentials.
5. On success the response must carry an `X-Condor-Features` header that contains `changeindication-port`. If it does not, the subscribe is treated as failed (`NOT_SUBSCRIBED`).

#### Receiving an event

1. One datagram is read into a 1024 byte buffer.
2. Datagram layout, line by line:
   - first line: `NOTIFY /di/v1/products/{productId}/{port} HTTP/1.1`
   - following lines: HTTP style headers
   - last line: the base64 encrypted payload
3. The port name is taken from the path in the first line and the sender is taken from the packet's source address.
4. The event is ignored unless the sender IP matches the node's IP.
5. The payload is decrypted with the C.4 pipeline.
6. If decryption fails, the port's listeners get an error and the port falls back to a full `GET` of the port to reload its state (C.12h).
7. Otherwise the event is matched against the port (C.12b), the decrypted JSON is merged into the cached properties (C.12d) and listeners are notified.

#### Automatic resubscribe

- When the TTL timer fires and the port is still subscribed, the same `POST` is sent again with the same TTL and the timer is rescheduled for another TTL.
- A change of the node's `bootId` (the device rebooted) also triggers a resubscribe.

#### Unsubscribe

1. The resubscribe timer is cancelled and the port is marked unsubscribed.
2. Unsubscribe request:

   ```
   DELETE /di/v1/products/{productId}/{port}

   {"subscriber": "deadbeef<random>"}
   ```

3. The UDP receiver stops only when no port is listening any more (C.12f). Stopping it closes the socket and releases the multicast lock.

### C.6 Transport switching (LAN and HSDP)

#### Switching rule

1. The combined transport listens to availability changes of each of its transports.
2. On any change, a pending switch is cancelled and a new one is scheduled 1000 ms later (a cool-down so that flapping does not cause repeated switches).
3. When it runs, the first available transport in order (LAN, then HSDP) is chosen.
4. If it is the same as before, nothing happens. If either the old or the new one is "none", an availability change is reported.
5. Every active subscription is moved: unsubscribed on the old transport and subscribed on the new one.

#### Availability

| Transport | Available when |
|---|---|
| LAN | the node has an IP address, Wi-Fi or Ethernet is connected, and the current SSID matches the stored SSID (or the stored SSID is unknown) |
| HSDP | internet is connected and the node has an `hsdpId` |

### C.7 HSDP authentication state machine

This is the connect sequence of the older HSDP cloud path (`HSDPController`). The sign-on decision tree is detailed in C.13.3.

1. Sign on:
   - cached token set present: use it and discover services;
   - no provisioned identity: bootstrap sign-on, service discovery, identity provisioning, then sign on again with the new identity;
   - provisioned identity present: reuse the cached access token if it has more than 120 s left, else request a new one with the identity's credentials, then discover services and start the expiry timer.
2. If sign-on fails: `AUTHENTICATION_FAILED`.
3. Build the control service:
   - take the discovered service tagged `IOT`;
   - pick its URL that starts with `wss://`;
   - the URL has the form `wss://host/path?topic-prefix=<prefix>`, giving the MQTT endpoint and the topic prefix;
   - the control service is created for this device's `hsdpId`, endpoint and topic prefix.
4. Failure checks, in this order:
   - no control service: `NO_CONTROL_SERVICE_AVAILABLE`
   - no access token: `MISSING_ACCESS_TOKEN`
   - no signed token: `MISSING_SIGNED_TOKEN`
5. Connect MQTT with the access token and the signed token. On failure: `CONNECT_FAILED`. On success the controller is connected.

#### Token refresh

1. When a new token is obtained, an expiry timer is started. If `expiresIn - 30` is negative, `TOKEN_EXPIRY_TOO_SHORT` is reported and no timer is started.
2. The timer fires after `expiresIn` seconds.
3. If a persisted token set exists, the tokens are refreshed with the refresh token, otherwise a full provisioned sign-on is done.
4. After a successful refresh the controller disconnects and reconnects with the new tokens.

### C.8 HSDP command execution (remote request)

This is what a port `GET` becomes when LAN is unavailable and HSDP is selected (`HSDPRemoteRequest`).

1. A random UUID is generated as the command identifier.
2. The request starts listening to all incoming MQTT control messages.
3. Command detail sent for a `GET` of port `air` on product 1:

   ```json
   {
     "condorVersion": "1",
     "op": "GetProps",
     "path": "1/air",
     "values": null
   }
   ```

   `path` is `{productId}/{port}`. Other operations use the same envelope with a different `op` (see C.12a for `ExecMethod` and C.12 for `Subscribe`).
4. Commands to the cloud are strictly serialized: a command is only sent after the previous one has completed. If the controller is not connected it connects first (C.7).
5. Wait up to 30 s for the MQTT publish to be acknowledged. On timeout: `SEND_FAILED`.
6. Wait up to 30 s for the device's answer. Incoming control messages are handled by type:

| Message type | Action |
|---|---|
| `accepted` | ignored, it only acknowledges receipt |
| `rejected` | the request ends as `REJECTED` |
| `notification` | if the command name equals the UUID from step 1, its status detail JSON is the answer |

7. The listener is removed.
8. If the command was rejected: `REJECTED`. If no answer arrived: `REQUEST_FAILED`.
9. The answer is a Condor control message. If its `status` is not 0, the status is mapped to an error code. Otherwise its `values` field, serialized back to JSON, is the result, and it is processed like a LAN response (merged into the cache).

### C.9 Firmware update state machine

Local firmware push to the `firmware` port (`/di/v1/products/0/firmware`, `FirmwareUpdatePushLocal`). Relevant port properties: `state`, `size`, `data`, `progress` (bytes received so far, not a percentage), `maxchunksize`, `statusmsg`.

State values on the wire: `idle`, `preparing`, `downloading`, `checking`, `ready`, `go` (also read as `programming`), `cancel` (also read as `canceling` or `cancelling`), `error`, `unknown`.

#### Start

1. A watchdog is armed for the caller's timeout. If it fires, the update fails with "Timed out".
2. The port is read to get its current state.
3. Depending on that state:
   - `idle`: go to "wait for downloading" below.
   - `downloading`, the app is resuming an interrupted upload, and the reported size equals the image size: continue uploading from the reported `progress`.
   - any other state (for example `error` or `checking`): `PUT {"state": "cancel"}`, then `PUT {"state": "idle"}`, then go to "wait for downloading".

#### Wait for downloading

1. `PUT {"state": "downloading", "size": <image size in bytes>}`.
2. Poll the port every 1 s for at most 10 s:
   - `downloading`: stop polling and start the upload at offset 0;
   - `error`: fail;
   - 10 s elapsed: fail with "Timed out while waiting for downloading state".

#### Chunk upload

1. Read `maxchunksize` from the cached port properties. Start with no size reduction.
2. Chunk size is `maxchunksize - 50 * reductions`.
3. Send the next slice of the image starting at the current offset: `PUT {"data": "<base64 chunk>"}`.
4. On success, read `progress` from the response:
   - `progress >= size`: wait for `ready` (below);
   - `state` is `error`: fail;
   - `state` is not `downloading`: fail with "No longer in Downloading state";
   - otherwise continue with the next chunk at offset `progress`.
5. On an `OUT_OF_MEMORY` error, increase the reduction count by one (50 bytes smaller) and retry the same offset.
6. Any other error fails the upload.

The device's `progress` value, not the app's own count, decides where the next chunk starts.

#### Wait for ready

If the state is already `ready`, the upload is done. Otherwise the app subscribes to the port and polls it. `ready` means success, `error` fails with the device's `statusmsg`.

#### Deploy

1. `PUT {"state": "go"}`.
2. Poll every 1 s until the caller's timeout:
   - `idle`: done, the device has rebooted into the new firmware;
   - `error`: deploy failed;
   - timeout: fails with "Timed out waiting for appliance".

#### Cancel

1. Stop the upload and all polling.
2. `PUT {"state": "cancel"}`.
3. Poll every 1 s until the caller's timeout. `error` means the cancel is complete. Timeout means the cancel failed.

#### State diagram

| From | Trigger | To |
|---|---|---|
| `idle` | `PUT {"state": "downloading", "size": N}` | `downloading` |
| `downloading` | all chunks uploaded | `ready` |
| `ready` | `PUT {"state": "go"}` | `go` (programming), then `idle` after the reboot |
| any state | `PUT {"state": "cancel"}` | `cancel`, then `error` |
| `cancel` | `PUT {"state": "idle"}` | `idle`, ready for a retry |

### C.10 Cloud pairing flow (HSDP control)

Pairs a device with the app's HSDP identity so it can be controlled through the cloud. It runs over the `pairing` port, product 0 (path `0/pairing`). Pairing port properties: `hsdpid`, `previousaction` (`Pair` or `Unpair`), `previousresult` (boolean), `previoustrustee`, `previoustype`.

1. Subscribe to the pairing port for change indications.
2. Once subscribed, call the `Pair` method with the type `control` and the app's HSDP identifier as trustee. Over LAN this is a `PUT` on the port (see C.12a). Over HSDP the command is:

   ```json
   {
     "condorVersion": "1",
     "op": "ExecMethod",
     "path": "0/pairing",
     "values": {"Pair": ["control", "<hsdpIdentifier>"]}
   }
   ```

3. The method answer is `{"return": [<n>]}`. A first value of 0 means the request was accepted. Any other value means rejected and the flow ends.
4. After acceptance, start a 30 s watchdog. If it fires, the flow ends with `TIMED_OUT`.
5. Wait for a change indication on the pairing port. Over HSDP this arrives as an MQTT `notification` whose operation is `ChangeIndication` (C.12).
6. Merge it into the cached pairing properties, then check:
   - `previoustype` is `control` and `previoustrustee` is our HSDP identifier, and
   - `previousresult` is true.
   Success if both hold, otherwise `REJECTED`.
7. Clean up: unsubscribe from the pairing port and cancel the watchdog.
8. On success, read the pairing port again and store its `hsdpid` on the node. That id is what HSDP commands are addressed to afterwards.

Unpairing is the same flow with the `Unpair` method. On success the stored `hsdpid` is cleared.

### C.11 LAN pairing flow (local authentication)

Obtains the `clientSecret` used by the PhilipsCondor scheme (C.3).

1. Wi-Fi must be available, otherwise the result is the error "no wifi".
2. If the node has no `clientId` yet, a random 128 bit value is generated, base64 encoded and stored as the `clientId`.
3. Request body: `{"id": "<base64 clientId>"}`. If the caller passes extra evidence fields, they are merged into the body, e.g. `{"id": "...", "key": "<evidence>"}` for the seed challenge.
4. Send it:

   ```
   PUT https://{ip}/auth/v{version}/
   Content-Type: application/json
   ```

   The same pinned TLS client as for normal requests is used.
5. Handle the answer:

| Result | Meaning |
|---|---|
| 200 | parse the JSON object; read `authenticated` (false if missing) and `secret`; if authenticated and a secret is present, store it as the `clientSecret`; any remaining fields (such as `seed`) go back to the caller |
| 400 | error `error_bad_request` |
| 502 | error `error_bad_gateway` |
| socket timeout | error `error_timeout` |
| TLS handshake or other I/O failure | error carrying the exception message |
| no Wi-Fi | error `no wifi` |

6. If the answer was not authenticated and contains a `seed`, the caller computes `evidence = SHA256(seed + clientId)` and repeats from step 3 with `{"key": evidence}` merged in.

#### Pairing patterns

| Case | Exchange |
|---|---|
| Device never paired | `PUT {"id": "abc123..."}` returns `{"authenticated": true, "secret": "xyz789..."}`. Store the secret. |
| Already paired, seed challenge | `PUT {"id": "abc123..."}` returns `{"authenticated": false, "seed": "qrs456..."}`. Compute the evidence, then `PUT {"id": "abc123...", "key": "<evidence>"}` returns `{"authenticated": true, "secret": "xyz789..."}`. Store the new secret. |
| Already paired, app holds no secret for it | The device answers with a seed. The earlier analysis records that the evidence cannot be computed without the existing `clientSecret`, so pairing fails. The device has to be factory reset or re-paired with the Android app. |

### C.12a ExecMethod wire format

#### LAN

```
PUT https://{ip}/di/v1/products/{productId}/{port}
Authorization: PHILIPS-Condor <credentials>
Content-Type: application/json

{"Pair": ["control", "hsdpIdentifier123"]}
```

The method name is the only JSON key and its value is the parameter list.

Response:

```json
{"return": [0.0]}
```

The key is always `return` and its value is the list of return values.

#### HSDP

```json
{
  "condorVersion": "1",
  "op": "ExecMethod",
  "path": "0/pairing",
  "values": {"Pair": ["control", "hsdpIdentifier123"]}
}
```

The answer is an MQTT `notification` whose `statusDetail.values` holds the same `{"return": [...]}` structure.

### C.12b Subscription event path matching

A port accepts an event if the event's path is either:

- the port name alone, e.g. `airfryer`, or
- `{productId}/{port}`, e.g. `1/airfryer`.

Both forms are needed: the app takes the bare port name from UDP `NOTIFY` events, while HSDP MQTT change indications carry `productId/port`.

### C.12c JSON encoding conventions

- Binary fields (such as the firmware `data` chunk) are encoded as base64 strings in JSON and decoded back from base64.
- Nulls are serialized and HTML escaping is off.
- JSON integers stay integers when parsed into generic maps (`42` is not turned into `42.0`).

### C.12d Property merge algorithm

Incoming data never replaces a port's cached state; it is merged into it:

1. Parse the incoming JSON object.
2. Take the cached properties as a JSON object.
3. For each incoming key whose value is not `null`, set that key in the cached object (overwrite or add). Keys with a `null` value are skipped and leave the cached value in place.
4. The result becomes the new cached state.

Example:

```
cached:   {"temp": 180, "status": "cooking", "time": 600}
incoming: {"temp": 200, "cur_time": 295}
merged:   {"temp": 200, "status": "cooking", "time": 600, "cur_time": 295}
```

`temp` is overwritten, `status` and `time` are kept, `cur_time` is added. The merge is shallow: a nested object in the incoming data replaces the cached one as a whole.

```python
def merge_props(cached: dict, incoming: dict) -> dict:
    merged = dict(cached)
    for key, value in incoming.items():
        if value is not None:
            merged[key] = value
    return merged
```

### C.12e LAN subscribe: UDP port wiring

1. Start (or reuse) the UDP receiver and get its bound port, 8080 normally.
2. If no port could be bound: fail with `NO_TRANSPORT_AVAILABLE`, message "Could not register UDP port".
3. Exchange the encryption key if needed.
4. Send `POST /di/v1/products/{productId}/{port}` with `{"subscriber": "deadbeef...", "ttl": 300, "changeudp": 8080}`, with the same 401 retry as other requests.
5. If the UDP socket could not be initialized: fail with `IOEXCEPTION` and the exception message.

### C.12f LAN unsubscribe: UDP receiver lifecycle

1. If this was the last port subscribed through this LAN transport, the UDP receiver is released. The receiver stops once no listener at all remains.
2. Exchange the encryption key if needed.
3. Send `DELETE /di/v1/products/{productId}/{port}` with `{"subscriber": "deadbeef..."}`.

All ports of one LAN transport share a single UDP receiver, so it only stops after every port has unsubscribed.

### C.12g Port callback batching

Gets and puts are queued differently:

- Get: callers that ask for the port's properties while a get is already queued or in flight are added to the same callback list. One HTTP request is made and every caller receives the same result. Example: three gets issued while the first is in flight produce one request and three identical answers.
- Put: each put is queued on its own and executed one after another in order.

When a request completes, the port is marked idle and the next queued operation is started (order as in C.2).

### C.12h Decryption failure during subscription

When a UDP event cannot be decrypted:

1. If the event's port matches (C.12b), the port's listeners receive an `IOEXCEPTION` error, "decryption failed".
2. The port then does a full `GET` of its properties. On success, listeners are notified with the reloaded state. This recovers the correct state after a lost event.

The decryption failure itself also triggers a new key exchange (C.4).

### C.12i Port property type resolution

Each port type maps to its own properties structure, and every JSON body for that port is parsed into it. This has no wire-level effect.

### C.12j Correction: HTTP responses are not decrypted in the transport

An earlier version of this analysis said the LAN transport decrypts every HTTP response body. That is wrong. Verified behavior:

- The default conversion of response bytes to JSON is a plain UTF-8 decode, and the LAN transport does not override it.
- AES decryption (C.4) is applied only to UDP subscription events, in the local subscription handler.
- The HTTP response body is read as a string and handed to the port's response processing unchanged. No step further down decrypts it either: the only caller of the decryptor is the local subscription handler. The request body is plain JSON as well, sent to an `https://` URL that the app builds with a fixed scheme.
- A decryption failure triggers a key re-exchange.

### C.12 HSDP subscription (cloud push events)

The HSDP equivalent of C.5.

#### Subscribe

1. The subscription handler reads the node's `hsdpId` and starts listening to all incoming MQTT control messages.
2. A remote request is sent with this command detail and handled like C.8 (send ACK, then answer):

   ```json
   {"condorVersion": "1", "op": "Subscribe", "path": "1/air", "ttl": 300}
   ```

#### Receiving an event

1. Messages whose type is not `notification` are ignored.
2. Messages whose status detail `op` is not `ChangeIndication` are ignored.
3. The status detail is parsed as a Condor control message. Its `path` (e.g. `1/air`) and its `values` (as JSON) are handed to the ports.
4. From there processing is the same as for LAN events: path matching (C.12b) and merge (C.12d).

#### Unsubscribe

The handler stops listening to MQTT messages and an unsubscribe command is sent over MQTT.

### C.13 HSDP token system

Token lifecycle of the older HSDP Condor path. FUSION devices do not use it (see C.14).

#### C.13.1 Token types

| Token | Obtained with | Purpose | Lifetime |
|---|---|---|---|
| bootstrap access token | client credentials grant with the app-wide bootstrap client | discovery and provisioning only | short |
| provisioned access token | password grant with the provisioned identity | main API token | `expiresIn` seconds |
| refresh token | returned with the provisioned access token | refresh without the password | longer than the access token |
| signed token | returned with the provisioned access token | JWT used to authenticate MQTT | same as the access token |

#### C.13.2 Credential data

| Set | Fields |
|---|---|
| bootstrap credentials (app-wide, built into the app) | `clientId`, `clientSecret` |
| provisioned identity (from provisioning) | `clientId`, `clientSecret`, `username` (the `loginId`), `password`, `hsdpIdentifier`, `identitySignature` |
| cached token set | `accessToken`, `refreshToken` (may be absent), `signedToken`, `accessTokenExpiresIn` |

Service base URLs:

- IAM: `https://iam.{region}.philips-healthsuite.com`
- Discovery: `https://discovery.{region}.philips-healthsuite.com`

The app also supplies a provisioning evidence map and can override the HSDP identifier.

#### C.13.3 Sign-on decision tree

1. **Cached token set present** (returning user):
   1. Load the tokens into the HTTP layer so that all requests carry the access token.
   2. Discover services.
   3. Start the expiry timer with the cached `accessTokenExpiresIn`.
   4. Done.
2. **No tokens, provisioned identity present**:
   1. If the in-memory access token has more than 120 s left, use it.
   2. Otherwise request a token with `grant_type=password` using the identity's `clientId`, `clientSecret`, `username` and `password`.
   3. Keep the response in memory and compute the absolute expiry as `expiresIn + now_in_seconds`.
   4. Persist the token set.
   5. Discover services.
   6. Start the expiry timer with `expiresIn`.
   7. Done.
3. **Nothing at all** (first run):
   1. Bootstrap sign-on: `grant_type=client_credentials` with the bootstrap `clientId` and `clientSecret`.
   2. Discover services: `GET {discovery}/services`, returning a list of `{tag, url}`. The `PRV` tag is the provisioning service.
   3. Provision: `POST {provisioning url}/identity` with the evidence map. The answer holds `oauthClientId`, `oauthClientSecret`, `loginId`, `password`, `hsdpId` and `identitySignature`, and all six must be present. They are stored as the provisioned identity.
   4. Run sign-on again, which now takes branch 2.

#### C.13.4 Token expiry timer

1. If `expiresIn - 30` is negative, report `TOKEN_EXPIRY_TOO_SHORT` to all listeners and do not start a timer.
2. Otherwise cancel any running timer and start one for `expiresIn` seconds.
3. When it fires:
   - with a persisted token set: refresh with `grant_type=refresh_token` (refresh token, `clientId`, `clientSecret`);
   - without one: do a full password sign-on (C.13.3 branch 2).
4. On success the MQTT controller disconnects and reconnects with the new tokens. On failure the controller reports a connection error `AUTHENTICATION_FAILED`.

| Constant | Value | Meaning |
|---|---|---|
| access token cache threshold | 120 s | a cached token with more time left than this is reused |
| access token invalidation window | 30 s | minimum lifetime for the timer to be started |

#### C.13.5 How the tokens are used for MQTT

1. Sign on (C.13.3).
2. Build the control service from the `IOT` service's `wss://` URL, split into endpoint and `topic-prefix` (C.7 step 3).
3. Take the access token and the signed token, preferring the persisted token set over the in-memory response.
4. Connect MQTT over WSS with both tokens as authentication.

#### C.13.6 IAM token endpoint

`POST {iam base}/authorize/oauth2/token`

| Grant type | Parameters | Returns |
|---|---|---|
| `client_credentials` | `client_id`, `client_secret` | access token only, no signed token |
| `password` | `client_id`, `client_secret`, `username`, `password` | access token, refresh token, signed token, `expiresIn` |
| `refresh_token` | `refresh_token`, `client_id`, `client_secret`, optional `scope` | new access token, refresh token, signed token, `expiresIn` |

#### C.13.7 Discovery and provisioning

- Discovery: `GET {discovery base}/services` returns a list of `{tag, url}`. Known tags: `PRV` (provisioning) and `IOT` (MQTT control).
- Provisioning: `POST {provisioning base}/identity` with the evidence map as body. Returns `oauthClientId`, `oauthClientSecret`, `loginId`, `password`, `hsdpId`, `identitySignature`.

#### C.13.8 Refresh is driven by the app's own timer

The HTTP client library's automatic token refresh is disabled: the policy it is given never refreshes (any refresh attempt returns "no refresh token"). Refreshing is done only by the timer in C.13.4. When a returning user signs on, the stored tokens are installed in the client so that every HTTP request carries the access token.

### C.14 FUSION/DaConnect authentication and MQTT

FUSION devices (newer cloud relay) use the DaConnect SDK path, not the HSDP Condor path of C.13. This section grew in stages. C.14.1 to C.14.13 record an earlier understanding built on a SAS token exchange, which was a working workaround in testing on 2026-03-26. C.14.14 records what the app actually does, confirmed on hardware on 2026-03-27: it uses the Gigya OIDC access token directly, and the MQTT client id is built from the `get-id` user id. C.14.14 supersedes the earlier subsections where they disagree.

#### C.14.1 SAS exchange flow (earlier understanding)

1. Gigya CDC login (email and OTP) gives a Gigya access token and id token.
2. SAS token exchange:

   ```
   POST {sasBackendUrl}/api/TokenExchange
   Accept: application/vnd.oneka.v2.0+json
   Content-Type: application/vnd.oneka.v2.0+json

   {"idToken": "<gigya id token>", "exchangeFor": "HSDP"}
   ```

   The answer holds `accessToken`, `refreshToken`, `signedToken`, `idToken`, `expiresIn`, `tokenType` and `federationFlow`.
3. MQTT signature, requested with the Gigya token, not a SAS token:

   ```
   GET {iotApiUrl}/api/da/user/self/signature
   Authorization: Bearer <gigya access token>
   ```

   Answer: `{"signature": "<mqtt signature>"}`.
4. MQTT over WSS to `wss://{mqttHost}:443/mqtt` with the custom authorizer headers: the token header carried the SAS `idToken` and the signature header carried the signature from step 3. The client id was the SAS `idToken`'s `sub` claim with its `@fed-...` suffix removed. C.14.14 corrects both the token and the client id.

#### C.14.2 SAS request

`POST` to `{sasBackendUrl}/api/TokenExchange`, with `Accept` and `Content-Type` both set to `application/vnd.oneka.v2.0+json`. Body fields: `idToken` (the Gigya id token) and `exchangeFor` (default `HSDP`). The app's interface for this is `SasApiService`.

#### C.14.3 SAS response

| Field | Content |
|---|---|
| `accessToken` | HSDP access token |
| `refreshToken` | HSDP refresh token |
| `signedToken` | HSDP signed token |
| `idToken` | SAS-issued HSDP JWT |
| `expiresIn` | seconds until expiry |
| `tokenType` | `Bearer` |
| `federationFlow` | federation flow type |

The `idToken` in the response is a new JWT issued by the SAS backend, not the Gigya id token that was sent. In the earlier testing it was this token that authenticated the MQTT connection.

#### C.14.4 Token exchange configuration

The app's configuration describes up to two exchanges, under the keys `hsdp` and `di`. Each has `channel` (`FRONT_CHANNEL` or `BACK_CHANNEL`), `federationFlow`, `url` (the exchange endpoint) and `resourceId`.

#### C.14.5 Signature response

`GET /api/da/user/self/signature` with the Gigya bearer token returns `{"signature": "<base64>"}`.

#### C.14.6 WebSocket URL

The broker URL is always `wss://{host}:443/mqtt`: port 443 and path `/mqtt`. Example host: `a3qn2avkfnous7-ats.iot.eu-west-1.amazonaws.com`.

#### C.14.7 MQTT connection info

The app keeps four values together for a connection: the access token, the MQTT signature from `/api/da/user/self/signature`, the tenant and the WebSocket URL. The earlier analysis read the access token here as the SAS HSDP token; C.14.14 shows it is the Gigya OIDC access token.

#### C.14.8 Client id from the SAS token (earlier understanding)

- `sub` claim example: `user123@fed-myphilipsonprod`.
- Using it as the client id prefix required dropping everything from `@` on (`user123`). With the suffix included, the broker answered CONNACK return code 2 (identifier rejected).
- The token header carries the full SAS `idToken`, unchanged.

C.14.14 replaces this: the client id prefix is the user id returned by `get-id`.

#### C.14.9 Cross-token pattern (earlier understanding)

In the SAS based approach, two token sources were needed:

| Token | Used for |
|---|---|
| Gigya CDC token | MQTT signature (`GET /api/da/user/self/signature`) and the DaConnect IoT REST API |
| SAS HSDP `idToken` | MQTT connect (token header) |

The custom authorizer rejected the OTP-derived Gigya token, and the signature endpoint rejected the SAS token.

#### C.14.10 FUSION MQTT connection details (live testing, 2026-03-26)

These details come from testing against real FUSION devices and protocol analysis, not from the decompiled app alone.

Transport:

- Endpoint: `wss://ats.prod.eu-da.iot.versuni.com:443/mqtt`, MQTT over secure WebSocket.
- Port 8883 is not usable: it requires mutual TLS client certificates and the app has none.
- On port 443 the WebSocket upgrade succeeds without authentication (HTTP 101). Authentication happens at MQTT CONNECT, through the custom authorizer.

WebSocket upgrade headers used in that test:

```
x-amz-customauthorizer-name: CustomAuthorizer
token-header: Bearer <SAS idToken>
x-amz-customauthorizer-signature: <mqtt signature>
Host: ats.prod.eu-da.iot.versuni.com:443
```

No `Origin` header is sent. The iOS app does not send one, and paho-mqtt adds one by default, so it has to be suppressed.

CONNECT parameters: client id `{sub}_{UUID}` (sub with the `@fed-...` suffix removed, UUID random per session), `clean_session` false, keepalive 30 s.

Topics: the same five subscriptions and three publish topics as in C.14.14.

Observed SAS token sizes and claims:

| Item | Observed |
|---|---|
| SAS `accessToken` | 27 characters, opaque |
| SAS `signedToken` | 344 characters |
| SAS `idToken` | 1478 character JWT |
| `idToken` `iss` | `https://iam-service.eu-west.philips-healthsuite.com/oauth2/access_token` |
| `idToken` `aud` | `21e431131cb04a0eb56` |
| `idToken` `sub` | `{gigya sub}@fed-digitalidentityonprod.{uuid}` |
| `federationFlow` | `DI_HSDP` |
| MQTT signature | 684 characters, base64 |

#### C.14.11 Authorizer combinations tested (2026-03-26, HD9285)

| Token header | Signature | Result |
|---|---|---|
| Gigya access token | Gigya signature | WebSocket closed (denied) |
| SAS access token | Gigya signature | WebSocket closed (denied) |
| SAS access token | SAS signed token | WebSocket closed (denied) |
| Gigya access token | SAS signed token | WebSocket closed (denied) |
| SAS id token | Gigya signature | CONNACK rc=2 (authorized, id rejected) |
| SAS id token | SAS signed token | CONNACK rc=2 (authorized, id rejected) |

Conclusions drawn at the time:

- Only the SAS `idToken` passed the custom authorizer. The Gigya token (an OTP-derived one, see C.14.14) and the SAS access token were rejected.
- Both the Gigya signature and the SAS signed token worked as the signature.
- CONNACK rc=2 meant authorization passed but the client id was rejected because of the `@fed-...` suffix. The fix then was to cut the `sub` at `@`.

#### C.14.12 FUSION service URLs

| Service | URL |
|---|---|
| SAS token exchange (primary) | `https://www.backend.vbs.versuni.com/api/TokenExchange` |
| SAS token exchange (alternative) | `https://www.home.id/api/sas/hsdp-token` |
| SAS token exchange (discovered) | from the space configuration key `tokenExchange.hsdp.url` |
| MQTT signature | `https://prod.eu-da.iot.versuni.com/api/da/user/self/signature` |
| MQTT broker | `wss://ats.prod.eu-da.iot.versuni.com:443/mqtt` |
| IoT API | `https://prod.eu-da.iot.versuni.com/api/` (used for FUSION instead of `air.acc.eu-da.iot.versuni.com`) |

`https://prod.eu-da.iot.versuni.com/api/da/user/self/hsdp-token` also exists but requires AWS SigV4 authentication, so it cannot be used from the integration.

#### C.14.13 iOS app traffic capture (February 2026)

- `User-Agent: NutriU/5 CFNetwork/3860.300.31 Darwin/25.2.0`
- The token is a Gigya JWT in the same format as the one produced by the OIDC flow.
- The WebSocket upgrade has no `Origin` header.
- The `Host` header includes the `:443` port suffix.

#### C.14.14 FUSION MQTT connection (APK-verified specification)

This consolidates the APK analysis and the confirmed hardware tests. It supersedes C.14.10 to C.14.13, which used SAS tokens.

##### Which token the app uses

The app uses the Gigya OIDC access token from its browser login (PKCE, `client_id=-u6aTznrxp9_9e_0a57CpvEG`) directly. There is no SAS exchange, no HSDP exchange and no transformation of the token.

How it gets there in the app, as pointers:

1. AppAuth stores the token response from the login. Its access token is a JWT whose audience includes `-u6aTznrxp9_9e_0a57CpvEG`.
2. When an MQTT connection is needed, `DaMqttClientImpl` asks its credential provider for the connection info.
3. The provider asks the client authentication provider for an access token. `FusionAuthenticationInitUseCaseImpl` takes it from `PhilipsUser`, whose "diAccessToken" (DI meaning Digital Identity, i.e. Gigya) comes through `DiDaBridgeImpl` from the AppAuth state.
4. That token goes into the connection info and from there into the `token-header` as `Bearer <token>`.

`PhilipsUser` exposes the Gigya OIDC access token, id token and access token expiry. In 8.16.0 these are obfuscated accessors (`l()`, `u()` and `X()` respectively).

##### CONNECT specification

Client id: `{userId}_{UUID}`, where the UUID is random per session. `userId` must be the id returned by `get-id` (see the confirmed test below), not the token's `sub` claim.

WebSocket upgrade headers:

| Header | Value |
|---|---|
| `x-amz-customauthorizer-name` | `CustomAuthorizer` |
| `x-amz-customauthorizer-signature` | signature from `GET .../user/self/signature` |
| `token-header` | `Bearer <Gigya OIDC access token>` |
| `content-type` | `application/json` |
| `tenant` | tenant from the FUSION configuration |

MQTT client options:

| Option | Value |
|---|---|
| connection timeout | 10 s |
| keepalive | 30 s |
| clean session | false |
| automatic reconnect | true |
| server URI | `wss://{mqttHost}:443/mqtt` |
| time to wait | 10000 ms |

The client object is first created with the placeholder server URI `wss://localhost.com`, and the real URI is supplied in the connect options.

Signature endpoint, called with the Gigya token added by an HTTP interceptor:

```
GET https://{platformRestApiBaseUrl}/api/{tenant}/user/self/signature
Authorization: Bearer <Gigya OIDC access token>
```

Answer: `{"signature": "..."}`, 684 base64 characters. The path used in the confirmed tests is `/api/da/user/self/signature`. The same interceptor (`ClientAuthenticationInterceptor`) adds the `authorization: Bearer <token>` header and contains a special check for URLs ending in `/user/self/get-id`.

##### Topics

Subscribe, QoS 0, per device:

```
$aws/things/{thingName}/shadow/get/accepted
$aws/things/{thingName}/shadow/update/accepted
$aws/things/{thingName}/shadow/get/rejected
$aws/things/{thingName}/shadow/update/rejected
{tenant}_ctrl/{thingName}/from_ncp
```

Publish, QoS 1:

| Topic | Use | Payload |
|---|---|---|
| `$aws/things/{thingName}/shadow/get` | request current shadow | empty |
| `$aws/things/{thingName}/shadow/update` | set state | `{"state": {"desired": {...}}}` |
| `{tenant}_ctrl/{thingName}/to_ncp` | NCP port commands | see below |

##### NCP port command format

```json
{
  "cid": "<UUID>",
  "time": {"utc": "2026-03-27 12:00:00.000"},
  "type": "command",
  "cn": "updatePort",
  "ct": "mobile",
  "data": {"portName": "airfryer", "properties": {"temp": 180}}
}
```

- `cn` (command name): `updatePort`, `getPort`, `setPort`, `getAllPorts`.
- `type`: `command` (app to device), `response`, `event`, `error`.
- Built in the app by `RemotePortCommand`.

##### Connection flow

1. Get credentials:
   1. Take the Gigya access token from the AppAuth state.
   2. Look up the signature in a cache keyed by that access token. On a miss, `GET {platform}/api/{tenant}/user/self/signature`.
   3. Take the access token again, since it may have been refreshed during the HTTP call.
   4. Assemble access token, signature, tenant and WSS URL.
2. Create the client once per session, with client id `{userId}_{UUID}` and in-memory persistence.
3. Connect with the five WebSocket headers and the options above.
4. Subscribe to the five topics of each device.
5. Request state: publish an empty payload to `shadow/get`. The shadow arrives on `shadow/get/accepted`.
6. Control the device: publish an NCP command to `to_ncp`. The answer arrives on `from_ncp`.

##### Reconnection

| Event | App behavior |
|---|---|
| connection lost | automatic reconnect, then resubscribe all devices |
| token refresh | disconnect, invalidate cached credentials, reconnect |
| app sent to background | unsubscribe the topics, keep the connection |
| app back in foreground | resubscribe all devices |
| connectivity change | wait 800 ms, then resume |

##### The two MQTT paths in the app

| | HSDP Condor (older) | DaConnect FUSION (newer) |
|---|---|---|
| App class | `hsdpclient/mqtt/MqttClientImpl` | `daconnect/device_control/mqtt/DaMqttClientImpl` |
| Token source | HSDP IAM (access token and signed token) | Gigya OIDC (access token and signature) |
| Token header | `AuthorizationToken: <accessToken>` | `token-header: Bearer <accessToken>` |
| Signature header | `x-amz-customauthorizer-signature: <signedToken>` | `x-amz-customauthorizer-signature: <mqttSignature>` |
| Authorizer name header | not set | `CustomAuthorizer` |
| Extra headers | none | `content-type`, `tenant` |
| Topics | `{topicBase}/crl/things/{hsdpId}/cmd/...` | `$aws/things/{thingName}/shadow/...` and `{tenant}_ctrl/...` |
| Client id | `hsdpId` | `{userId}_{UUID}` |
| Clean session | library default (true) | false |

FUSION devices use the DaConnect path.

##### Constants

| Name in the app | Value |
|---|---|
| custom authorizer name | `CustomAuthorizer` |
| authorizer name header | `x-amz-customauthorizer-name` |
| authorizer signature header | `x-amz-customauthorizer-signature` |
| token header | `token-header` |
| tenant header | `tenant` |
| content type header | `content-type`, value `application/json` |
| token prefix | `Bearer` |
| placeholder server URI | `wss://localhost.com` |
| connection timeout | 10 s |
| keepalive | 30 s |
| publish command timeout | 10 |
| quiesce timeout on disconnect | 200 ms |
| time to wait | 10000 ms |
| wait after connectivity change | 800 ms |

The Eclipse Paho MQTT classes are obfuscated in this app version (for example `MqttAsyncClient` is `h` and `MqttConnectOptions` is `j`).

##### OAuth clients in the app

The Android HomeID app embeds two OAuth clients:

| Client | client_id | Use |
|---|---|---|
| HomeID | `-u6aTznrxp9_9e_0a57CpvEG` | main app login through AppAuth |
| NutriU | `21e431131cb04a0eb56` (plus a client secret embedded in the app) | HSDP and FUSION configuration |

The domain configuration passes the NutriU values to the DaConnect SDK as `spectreHsdpIamAuthClientId`, `spectreHsdpIamAuthClientSecret` and `spectreHsdpIamRedirectUri` (`com.philips.apps.nutriu.21e431131cb04a0eb56://oauthredirect`). Pointers: `DomainBuilderKt`, `BackendConfigKt`.

The HSDP SSO deep link (`HsdpDeepLinkActivity`) also uses the NutriU client:

```
https://iam-service.eu-west.philips-healthsuite.com/authorize/oidc/login
  ?client_id=21e431131cb04a0eb56
  &redirect_uri=com.philips.apps.nutriu.21e431131cb04a0eb56://oauthredirect
  &response_type=code
  &provider=myphilipsonprod
```

The SAS `idToken` that passed the authorizer in the 2026-03-26 testing had `aud=21e431131cb04a0eb56`, the NutriU client.

The NutriU client is not what feeds MQTT. The main AppAuth login uses the HomeID client, so the access token the app puts in `token-header` has the HomeID audience. The NutriU client belongs to the separate HSDP SSO deep link flow, whose tokens are not stored in the AppAuth state. The analysis at this stage listed three options for the integration: refresh with the HomeID client (what the app does), refresh with the NutriU client, or keep the SAS exchange. The tests below settled it: the HomeID client's token works, and the NutriU refresh is not possible.

##### Live test results (2026-03-27, run v13, HD9285 of tester LucaTomei)

1. All 11 combinations tried were rejected with a WebSocket close (code 0), including the SAS `idToken` that had reached CONNACK rc=2 the day before.
2. The Gigya OIDC token's `aud` claim is an array of four values, not a single string: `6FrQfddqSE4-_teIbXQNihK3`, `cwbz2ncRQMBxzObB0g8_6UcL`, `DyUXxsatp6MbAmiYX561d-B6`, `-u6aTznrxp9_9e_0a57CpvEG`.
3. Refreshing with the NutriU client fails with HTTP 403 `invalid_client`. Gigya OIDC refresh tokens are bound to the client they were issued to, so a HomeID token cannot be refreshed as NutriU.
4. The HSDP SSO flow did not produce an authorization code (the browser flow was blocked).
5. The signature endpoint worked with the Gigya token (HTTP 200, 684 characters).
6. The SAS exchange worked (token sizes as in C.14.10).

The authorizer now rejected at the WebSocket level before processing CONNECT, which suggested its configuration had changed since 2026-03-26.

Why the earlier tests had failed: they used OTP-derived Gigya tokens, which carry no audience, while the app uses browser OIDC tokens with a proper audience, and the authorizer checks the audience.

| Token | Result |
|---|---|
| OTP-derived Gigya token (no `aud`) | rejected |
| browser OIDC Gigya token | accepted, confirmed in run v15 below |
| SAS `idToken` (`aud=21e431131cb...`) | accepted on 2026-03-26, not what the app uses |

##### MQTT connection confirmed (2026-03-27, run v15, same HD9285)

MQTT CONNECT succeeded once the client id prefix was taken from the `get-id` endpoint instead of the `sub` claim:

```
POST /api/da/user/self/get-id
{"idToken": "<Gigya OIDC id token>"}

-> {"userId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"}
```

| Client id | Length | Result |
|---|---|---|
| `{get-id userId}_{UUID}` | 73 characters | connected |
| `{sub}_{UUID}` | 69 characters | WebSocket closed |

The `get-id` user id is a dashed UUID, while the JWT `sub` is a 32 character hex string. The HTTP 101 upgrade succeeded in both cases, so the authorizer accepted both. The rejection came from the IoT policy: its `iot:Connect` resource matches the `get-id` user id, not the Gigya `sub`.

Confirmed full flow:

1. Token: Gigya OIDC access token from `cdc.accounts.home.id`, client `-u6aTznrxp9_9e_0a57CpvEG`.
2. Signature: `GET /api/da/user/self/signature` with `Authorization: Bearer <access token>`.
3. User id: `POST /api/da/user/self/get-id` with `{"idToken": "<id token>"}`, returning `{"userId": "..."}`.
4. Client id: `{userId}_{UUID}`.
5. WebSocket upgrade: `Host`, `Upgrade`, `Connection`, the `Sec-WebSocket-*` headers and the five auth headers only. No `Origin`, no `User-Agent`, no `Accept-*`.
6. MQTT 3.1.1, clean session false, keepalive 30 s, no username or password.

Other fixes that were needed to get there:

- remove the `Origin` header (Python paho-mqtt adds it, the app's Java Paho client does not);
- OAuth scope `consent`, singular, not `consents`;
- use a full UUID in the client id, not a short hex string;
- refresh the token before testing, since Gigya tokens expire after about an hour.

##### Shadow get confirmed (2026-03-27, run v15)

After CONNECT, all five topics were subscribed and an empty `shadow/get` was published. The device shadow arrived (819 bytes). HD9285 in standby:

```json
{
  "state": {
    "desired": {"sub": 1},
    "reported": {
      "ncpFirmwareVersion": "1.1.8",
      "hostFirmwareVersion": "0.5.6",
      "productState": "standby",
      "productError": "0",
      "powerOn": false,
      "connected": true,
      "timezones": {
        "posix": "CET-1CEST,M3.5.0,M10.5.0/3",
        "iana": "Europe/Rome"
      },
      "locale": "it-IT",
      "fcRst": false,
      "sub": 1
    }
  },
  "metadata": {
    "desired": { ... },
    "reported": {
      "ncpFirmwareVersion": {"timestamp": 1774633582},
      ...
    }
  },
  "version": 190,
  "timestamp": 1774642127
}
```

The `metadata` blocks were elided in the original capture, so this example is not valid JSON as shown.

Shadow compared with the local API:

- The shadow is flat under `state.reported` (e.g. `reported.productState`). The local API nests properties per port (`state.properties.airfryer.<property>`).
- Shadow properties seen in standby: `productState`, `powerOn`, `connected`, `productError`, `ncpFirmwareVersion`, `hostFirmwareVersion`, `timezones`, `locale`, `fcRst`, `sub`.
- Cooking properties do not come from the shadow but from NCP port commands.

##### NCP port discovery (2026-03-28, HD9285)

`getAllPorts` returned seven ports:

| Port | Access | Note |
|---|---|---|
| `push_c` | write | |
| `recipe_c` | write | |
| `UserServer` | write | |
| `UserClient` | write | |
| `Status` | read | main airfryer status port |
| `Control` | write | |
| `Config` | read | |

NCP port names compared with the local HTTP API:

| NCP port | Local API port |
|---|---|
| `Status` | `airfryer` |
| `Control` | `control` |
| `Config` | `config` |

`getPort` on `Status` with the device in standby:

```json
{
  "portName": "Status",
  "properties": {
    "status": "standby",
    "prev_stat": "pause",
    "temp": 180,
    "time": 60,
    "cur_time": 60,
    "error": 0,
    "preset": 0,
    "drw_opn": false,
    "temp_unit": false,
    "shk_rm_act": false,
    "recipe_id": "",
    "step_id": "",
    "SesOwnId": 0,
    "timestamp": ""
  }
}
```

NCP property names compared with the local API:

| NCP | Local API |
|---|---|
| `drw_opn` | `drawer_open` |
| `shk_rm_act` | `shake` |
| `prev_stat` | `prev_status` |
| `temp_unit` | no equivalent |
| `SesOwnId` | no equivalent |
| `timestamp` | no equivalent |

Same name on both sides: `status`, `temp`, `time`, `cur_time`, `error`, `preset`, `recipe_id`, `step_id`.

NCP status codes (`NcpStatusCode`):

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | device busy, retry |
| 2 | JSON error |
| 3 | correlation id error |
| 4 | client type error |
| 5 | time error |
| 6 | message type error |
| 7 | command name error |
| 8 | port error (missing or invalid port, or missing property) |
| 9 | write to a read-only port |
| 10 | read from a write-only port |
| 11 | serialization error |
| 12 | MQTT error |

##### Token refresh before use

Before handing out the access token for FUSION (`FusionAuthenticationInitUseCaseImpl`), the app:

1. Fails with "Not logged in" if the user is not logged in.
2. Treats the token as expired if the stored expiry is missing or zero, or earlier than the current time. The expiry is kept in milliseconds and compared with the current epoch time in milliseconds.
3. If expired, refreshes the OIDC tokens through AppAuth before continuing:

   ```
   POST {oidc token endpoint}
   grant_type=refresh_token
   client_id=-u6aTznrxp9_9e_0a57CpvEG
   refresh_token=<stored refresh token>
   ```

   The stored access token, id token and expiry are replaced with the new ones.

The app's authentication failure handler performs the same refresh and then reads the access token again. The refreshed token is still a Gigya OIDC access token for the HomeID client, and it is the same refresh request (same client id and token endpoint) that the integration's `cloud_api.refresh_tokens()` sends.

## Cloud login (Gigya OIDC)

The app signs the user in through a browser based OAuth 2.0 / OpenID Connect flow against the Gigya CDC identity provider. The tokens it gets back are the starting point for the SAS token exchange described in the MQTT custom authorizer section.

### Provider and client configuration

Gigya CDC OIDC provider:

| Item | Value |
|------|-------|
| Issuer | `https://cdc.accounts.home.id/oidc/op/v1.0/4_JGZWlP8eQHpEqkvQElolbA` |
| Authorization endpoint | `{issuer}/authorize` |
| Token endpoint | `{issuer}/token` |
| Discovery document | `{issuer}/.well-known/openid-configuration` |
| Gigya API key | `4_JGZWlP8eQHpEqkvQElolbA` |

HomeID app OAuth client:

| Item | Value |
|------|-------|
| Client ID | `-u6aTznrxp9_9e_0a57CpvEG` |
| Redirect URI | `com.philips.ka.oneka.app.prod://oauthredirect` |
| Response type | `code` |
| PKCE code challenge method | `S256` |

The full scope list the app requests (space separated in the request):

```
openid profile email offline_access
DI.Account.read DI.AccountProfile.read DI.AccountProfile.write
DI.AccountGeneralConsent.read DI.AccountGeneralConsent.write
DI.GeneralConsent.read DI.GeneralConsent.write
VoiceProvider.read VoiceProvider.write
subscriptions consents profile_extended
DI.AccountSubscription.write DI.AccountSubscription.read
```

Only `openid profile email offline_access` is needed for device access.

A second client exists for the NutriU / Alexa integration:

| Item | Value |
|------|-------|
| Client ID | `21e431131cb04a0eb56` |
| Client secret | embedded in the app, not reproduced here |
| Redirect URI | `com.philips.apps.nutriu.21e431131cb04a0eb56://oauthredirect` |

### Login sequence

1. OIDC discovery. `GET {issuer}/.well-known/openid-configuration` and read `authorization_endpoint`, `token_endpoint` and `userinfo_endpoint` from the response.

2. Authorization request, opened in a browser. `GET {authorization_endpoint}` with these query parameters:

   | Parameter | Value |
   |-----------|-------|
   | `client_id` | `-u6aTznrxp9_9e_0a57CpvEG` |
   | `response_type` | `code` |
   | `redirect_uri` | `com.philips.ka.oneka.app.prod://oauthredirect` |
   | `scope` | `openid profile email offline_access` |
   | `state` | random value |
   | `code_challenge` | SHA-256 of the code verifier (S256) |
   | `code_challenge_method` | `S256` |
   | `ui_locales` | user locale |

   After the user logs in and consents, the browser is redirected to `com.philips.ka.oneka.app.prod://oauthredirect?code={auth_code}&state={state}`.

3. Code exchange. `POST {token_endpoint}` with `Content-Type: application/x-www-form-urlencoded` and the form fields `client_id`, `grant_type=authorization_code`, `code`, `redirect_uri` (same value as in step 2) and `code_verifier`. The response looks like this:

   ```json
   {
     "access_token": "...",
     "id_token": "...",
     "refresh_token": "...",
     "token_type": "Bearer",
     "expires_in": 300
   }
   ```

4. SAS token exchange (OIDC tokens to HSDP tokens). `POST https://www.home.id/api/sas/hsdp-token`, or the alternative `https://www.backend.vbs.versuni.com/api/sls/hsdp/token`, with these headers:

   ```
   Authorization: Bearer {OIDC access_token}
   Accept: application/vnd.oneka.v2.0+json
   Content-Type: application/vnd.oneka.v2.0+json
   ```

   and this body:

   ```json
   {"idToken": "{OIDC id_token}", "exchangeFor": "HSDP"}
   ```

   The response carries the HSDP token set described in the SAS section.

5. IoT API. `GET https://air.acc.eu-da.iot.versuni.com/api/user/self/device` with `Authorization: Bearer {HSDP accessToken}` returns the user's devices together with their credentials.

For reference, the PKCE pair from step 2 and 3 can be produced like this (standard RFC 7636, nothing app specific):

```python
import base64, hashlib, secrets

verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
challenge = base64.urlsafe_b64encode(
    hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
```

### Where each token goes

| Token | Source | Used for |
|-------|--------|----------|
| `access_token` | OIDC | `Authorization` header of the SAS exchange |
| `id_token` | OIDC | `idToken` in the SAS exchange body |
| `refresh_token` | OIDC | refresh at the OIDC token endpoint |
| `accessToken` | HSDP | `Authorization` header of the IoT API |
| `idToken` | HSDP | MQTT custom authorizer |
| `signedToken` | HSDP | some API calls |
| `refreshToken` | HSDP | HSDP token refresh |

### Backend URLs

| Service | URL |
|---------|-----|
| Backend API | `https://www.backend.vbs.versuni.com/api/` |
| Home.ID API | `https://www.home.id/api/` |
| Home.ID web | `https://www.home.id/` |
| DiDa auth UI | `www.accounts.home.id/authui` |
| HSDP IAM | `https://iam-service.eu-west.philips-healthsuite.com` |
| HSDP IAM token | `https://iam-service.eu-west.philips-healthsuite.com/authorize/oauth2/token` |
| HSDP discovery | `https://discovery.eu01.iot.hsdp.io/core/discovery` |
| IoT API | `https://air.acc.eu-da.iot.versuni.com/api/` |
| SAS token exchange | `https://www.home.id/api/sas/hsdp-token` |
| SAS token exchange (alternative) | `https://www.backend.vbs.versuni.com/api/sls/hsdp/token` |

### SAS exchange requirements

- The `Authorization` header must carry the OIDC `access_token`, not the `id_token`.
- The body must carry the OIDC `id_token`, and its `aud` claim must be present and valid.
- Tokens obtained from Gigya `accounts.getJWT` have `aud` set to null and are rejected.

### Why the email OTP login does not give device access

The email OTP login uses Gigya `accounts.auth.otp.email.login`. It returns a Gigya session token, not OIDC tokens. Turning that session into a JWT with `accounts.getJWT` gives a token with a null `aud`, which the SAS API rejects. Because OTP skips the OAuth consent step it cannot produce OIDC tokens the SAS API accepts. An OTP session can still list the user's devices in the cloud, but it cannot fetch their credentials through the SAS/HSDP path.

### Token refresh

`POST {token_endpoint}` with `Content-Type: application/x-www-form-urlencoded` and the form fields `client_id=-u6aTznrxp9_9e_0a57CpvEG`, `grant_type=refresh_token` and `refresh_token={OIDC refresh_token}`.

### APK pointers

| Topic | File in the decompiled app |
|-------|----------------------------|
| OAuth configuration | `ka/oneka/di/da/repository/DiDaRepository.java` (around line 1153) |
| Scopes | `ka/oneka/di/da/constants/DiDaConstants.java` (around line 18) |
| Backend URLs | `ka/oneka/app/multimodule/backend/BackendConfigKt.java` (around line 22) |
| SAS API service | `ka/oneka/backend/other/SasApiService.java` |
| SAS auth header | `ka/oneka/backend/other/SasAuthorizationInterceptor.java` |
| SAS request body | `ka/oneka/backend/data/response/GetSasHsdpTokenDataRequest.java` |
| SAS response | `ka/oneka/backend/data/response/SasHsdpTokensResponse.java` |
| DiDa bridge | `ka/oneka/di/da/DiDaBridgeImpl.java` |
| User object | `ka/oneka/domain/philips_user/PhilipsUserImpl.java` |
| AppAuth state | `net/openid/appauth/a.java` |
| IoT API base URL | `ka/oneka/backend/di/module/OthersApiModule.java` (around line 76) |

## Other app classes

These classes hold no protocol logic of their own. Only the ones that still tell us something about the wire format are kept. Thread wrappers, queue listeners, the Android context and handler providers, logging wrappers, stream copy helpers and the traffic tagging constants are left out.

### F.1 Port property classes

Each port has a `*PortProperties` class that only maps JSON keys. Port names and product IDs are listed in the port table elsewhere in this document.

| Port | Class | What it holds |
|------|-------|---------------|
| `backend` | `BackendPortProperties` | backend server URLs, connection status, cloud configuration (about 30 fields) |
| `bleparams` | `BleParamsPortProperties` | BLE advertising interval, connection parameters, scan settings |
| `fac` | `FacPortProperties` | factory test settings |
| `locale` | `LocalePortProperties` | `locale`, a string such as `"en_US"` |
| `log` | `LogPortProperties` | device log entries |
| `logsettings` | `LogSettingsPortProperties` | log level, log destination, log upload settings |
| `time` | `TimePortProperties` | `utc` (Unix time in seconds) and `zone` (time zone name such as `"Europe/Amsterdam"`) |
| `transport` | `TransportPortProperties` | transport layer status, protocol version |
| `wifi` | `WifiPortProperties` | see below |
| `wifinetworks` | `WifiNetworksPortProperties` | scan result, see F.7 |
| `wifiui` | `WifiUiPortProperties` | Wi-Fi setup wizard state, progress and errors |

Keys of the `wifi` port:

| Key | Type | Meaning |
|-----|------|---------|
| `ssid` | string | network name |
| `password` | string | network password |
| `security` | string | for example `"WPA2"` or `"OPEN"` |
| `ciphers` | string | cipher list |
| `ip` | string | device IP address |
| `gateway` | string | gateway address |
| `netmask` | string | netmask |
| `dhcp` | boolean | DHCP enabled |

### F.2 Serializing port properties for PUT

Before a PUT, the app turns the properties object into a plain JSON map (`IntegerPreservingMapParserKt`, called from `CondorPort.propertiesToMap()`). Two things matter on the wire: integer values are sent as integers (`5`, not `5.0`), and keys whose value is null are left out of the body entirely.

### F.5 StateWaitException

Raised during a firmware update when the wait for a device state transition times out.

### F.6 and F.7 Wi-Fi scan result

The `wifinetworks` port returns an object keyed by SSID rather than a list:

```json
{"MyNetwork": {"channel": "6", "quality": 80, "security": "WPA2"}}
```

The app turns each entry into a `WiFiNode` with `ssid` (the key), `channel` (string), `quality` (integer, 0 to 100) and `security` (for example `"WPA2"` or `"OPEN"`). The conversion is done by `WifiNetworkPortResponseDeserializer`.

### F.8 DiscoverInfo

What the app records for each discovered device: CPP ID, IP address, device type, model ID, name, boot ID and HSDP ID.

### F.9 and F.10 Local network node database

When no secure database helper is configured, the app stores network nodes in an unencrypted SQLite database named `network_node.db`. Rows are written as upserts (insert with replace on conflict). `NetworkNodeDatabaseFactory` picks the secure helper first and falls back to this one. `NullApplianceDatabase` is a no-op implementation of the appliance store.

### F.17 Base64Adapter

Byte array fields on Condor ports are carried as Base64 strings. The app encodes them without line wrapping and decodes them with the default Base64 decoder.

### F.18 Poller

The poller reports every successful poll result and signals a timeout once its end time is reached.

### F.19 and F.20 Firmware update phases

`FirmwareUpdateOperation` exposes four steps: start, deploy and cancel, each with a timeout in milliseconds, plus finish. `FirmwareUpdateListener` reports these events: new firmware available (with its version), download progress (bytes uploaded out of total size), download finished or failed, checking progress, deploy finished or failed. A cancel ends in cancel finished or cancel failed.

### F.21 RemoteRequestType

Method names used for remote requests, all upper case: `PUTPROPS`, `GETPROPS`, `ADDPROPS`, `DELPROPS`, `SUBSCRIBE`, `UNSUBSCRIBE`. This is a separate set from `CondorOperation`, which uses the mixed case form such as `PutProps`.

### F.22 HSDP identifier

The HSDP identifier of a device is the custom identifier when one is configured, otherwise the identifier of the provisioned identity, otherwise none (`HSDPConfigurationKt`).

### F.23 HSDP control pairing

`HSDPControlPairingHandler` has two operations, pair and unpair. The implementation is `HSDPControlPairingHandlerImpl`, documented with the HSDP pairing classes.

### F.28 Multicast lock

Android drops incoming multicast packets unless a Wi-Fi multicast lock is held. The app takes a reference counted lock named `CondorMulticastLock` (`MulticastLockControlPoint`).

## Appendix: obfuscated name map

The APK is shrunk with ProGuard/R8, so library classes show up under short generated names in a JADX decompile. The tables below are the ones worth knowing when searching the decompiled source again. They were taken from app version 8.16.0 and only hold for that build.

### OkHttp (package `ix0`)

| Obfuscated | Real class |
|------------|-----------|
| `ix0.z` | `OkHttpClient` |
| `ix0.z.a` | `OkHttpClient.Builder` |
| `ix0.b0` | `Request` |
| `ix0.b0.a` | `Request.Builder` |
| `ix0.c0` | `RequestBody` |
| `ix0.d0` | `Response` |
| `ix0.e0` | `ResponseBody` |
| `ix0.u` | `Headers` |
| `ix0.x` | `MediaType` |
| `ix0.p` | `Dispatcher` |
| `ix0.g0` | `ConnectionSpec` |
| `ix0.l` | `ConnectionSpec.Builder` |
| `ix0.i` | `CipherSuite` |

Client builder methods that matter for timeouts and TLS:

| Obfuscated | Real method |
|------------|-------------|
| `R(t, unit)` | `readTimeout` |
| `g(t, unit)` | `connectTimeout` |
| `U(t, unit)` | `writeTimeout` |
| `e(t, unit)` | `callTimeout` |
| `S(sf)` | `socketFactory` |
| `T(sf, tm)` | `sslSocketFactory` |
| `O(hv)` | `hostnameVerifier` |
| `i(specs)` | `connectionSpecs` |

On `Request.Builder`, `p`/`q` set the URL, `i(method, body)` sets the method, `g` sets a header and `a` adds one. `client.a(request)` is `newCall`.

### Other recurring names

| Obfuscated | Meaning |
|------------|---------|
| `kotlin.jvm.internal.s` | Kotlin `Intrinsics` (null checks, `areEqual`), safe to skip when reading |
| `gw.c` | The app's logger; its one-letter methods are the log levels |
| `vv0.o` | Kotlin `Regex` |
| `ns0.a` | Kotlin `EnumEntries` |

### Inlined string constants

The decompiler sometimes prints an unrelated library constant where the app uses a string literal with the same value. These are protocol strings, not references to those libraries:

| Shown as | Actual string |
|----------|---------------|
| `Constants.IPC_BUNDLE_KEY_SEND_ERROR` | `"error"` |
| `RemoteSettings.FORWARD_SLASH_STRING` | `"/"` |
| `Headers.CONN_DIRECTIVE` | `"Connection"` |
| `ClientCookie.PATH_ATTR` | `"path"` |
| `ClientCookie.PORT_ATTR` | `"port"` |
| `ClientCookie.VERSION_ATTR` | `"version"` |

### Android Base64 flags

| Flag | Value | Meaning |
|------|-------|---------|
| `DEFAULT` | 0 | Standard base64, line breaks on encode, decoding accepts any |
| `NO_PADDING` | 1 | No trailing `=` |
| `NO_WRAP` | 2 | No line breaks |

## Appendix: app source map

Pointers into the decompiled app (8.16.0) for protocol work. Paths are relative to `com/philips/`.

### Condor SDK (`connectivity/condor/`)

- `core/request/Error`: error codes and names.
- `core/request/RequestQueue`: one request at a time per device, key exchange first.
- `core/communication/CombinedCommunicationStrategy`: LAN/cloud selection, 1000 ms cool-down, resubscribe on switch.
- `core/communication/ObservableCommunicationStrategy`: subscribe and unsubscribe payloads.
- `core/util/AppIdProvider`: `deadbeef` app ID.
- `core/util/Poller`: fixed interval port polling with a deadline.
- `core/port/common/DevicePort`, `DevicePortProperties`: `device` port.
- `core/port/common/HsdpPairingPort`, `HsdpPairingHandler`, `HsdpPairingPortProperties`: pairing port and flow.
- `core/port/common/WiFiNode`, `WifiNetworkPortResponseDeserializer`: Wi-Fi scan result format.
- `core/port/firmware/FirmwarePortProperties`: firmware port keys and states.
- `core/port/firmware/operation/FirmwareUpdatePushLocal`, `core/port/firmware/util/FirmwareUploader`: local firmware upload state machine and chunked upload.
- `core/port/BinaryCondorPort`: `.b` binary ports.
- `lan/communication/SubscribeRequest`: `changeudp` and the `X-Condor-Features` check.
- `lan/communication/FasterFirmwareUploadRequest`: raw binary firmware PUT.
- `lan/subscription/UdpReceivingThread`, `UdpEventReceiver`: UDP push socket and packet parsing.
- `lan/discovery/`: SSDP/mDNS discovered device data.
- `hsdp/HSDPController`: HSDP sign on, `IOT` service lookup, MQTT connect.
- `hsdp/HSDPRemoteSubscriptionHandler`: `ChangeIndication` over MQTT.
- `hsdp/HSDPControlPairingHandlerImpl`: pairing with type `control`.
- `hsdp/messages/CondorKey`, `CondorOperation`: message keys and `op` names.
- `hsdp/HSDPControlConnectionError`, `HSDPAuthenticationError`: HSDP error values.

### DaConnect SDK (`cl/daconnect/`)

- `authentication/`: token providers and MQTT connection info.
- `device_control/`: MQTT device control and remote commands.
- `device_management/`: BLE and SoftAP provisioning.
- `iot/`: REST client for the Philips IoT cloud (device registration, firmware, analytics).

### HSDP client (`connectivity/hsdpclient/`)

- `api/service/ControlServiceV1`: MQTT device control service.
- `api/service/IdentityAccessManagementServiceV2`: IAM token endpoints.
- `api/service/DiscoveryServiceV1`: service discovery.
- `api/service/ProvisioningServiceV1`: device provisioning.
- `authorization/PassiveRefreshPolicy`: token refresh with a refresh token.
- `api/model/ControlModel`: MQTT command and received message models.
- `mqtt/`: wrapper around the Paho MQTT client.
- `generated/`: Swagger generated models for the HSDP APIs.

### App (`ka/oneka/`)

The UI layer holds no protocol implementation of its own and goes through the two SDKs above. The packages of interest are `backend/` (Gigya CDC and HSDP IAM bridge clients), `fusion/` (FUSION device bridge and onboarding) and `connect/` (pairing wizard UI).

## Appendix: corrections

Points where an earlier reading of the app was wrong or incomplete, settled by a later line by line check of the condor sources and, where noted, the smali:

- `CombinedCommunicationStrategy` was described too briefly. It waits a 1000 ms cool-down after any availability change, and when the best transport changes it moves every active subscription to the new one. Subscription listeners and enable/disable calls go to all transports, not only the active one.
- The full set of transport operations includes add properties and delete properties besides get, put, subscribe, unsubscribe and method calls.
- The Wi-Fi scan entry (`WiFiNode`: `channel`, `quality`, `security`, `ssid`) was missing, and the `wifinetworks` reply is an object keyed by SSID that the app turns into a list.
- The single thread executor used on the LAN side is not only a debug aid: it counts running tasks so the LAN strategy can tell when its queue has drained.
- LAN subscribe requests add `changeudp` with the local UDP port, and the device signals UDP push support with the `X-Condor-Features: changeindication-port` header.
- The raw binary firmware upload has no usable URL in the APK (it resolves to host `{ip}firmwaredata`, path `/`); there is no evidence for a `/firmwaredata` path on the device.
- The UDP receiver keeps the product ID in the port identifier (`1/air`, not `air`). Checked against the smali.
- The `Pair`/`Unpair` method parameters are `[type, trustee]` (for cloud control: `control` and the app's HSDP identifier), not an HSDP ID and a secret. Checked against the smali.
- No device traffic over HTTP is encrypted at the payload level, and the app never uses plain HTTP for it. LAN request URLs are built with a fixed `https://` scheme, request bodies are plain JSON, response bodies are passed on unchanged, and the `https` database flag is always written as true and never read. The only AES on device data is the decryption of UDP change events. Checked against the smali.
