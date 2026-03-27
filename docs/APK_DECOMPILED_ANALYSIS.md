# Philips HomeID APK - Complete Decompiled Code Analysis

**APK Version:** 8.16.0 (com.philips.ka.oneka.app)
**Decompiler:** JADX
**Analysis Date:** 2026-03-26

This document provides a line-by-line annotated analysis of every relevant subsystem in the decompiled Philips HomeID APK. The goal is to document the complete protocol implementation so it can be reproduced independently.

---

## Table of Contents

0. [Protocol Quick Start: Talk to a Device in 5 Minutes](#0-protocol-quick-start-talk-to-a-device-in-5-minutes)
1. [Architecture Overview](#1-architecture-overview)
2. [Package Structure](#2-package-structure)
3. [Core Data Model: NetworkNode](#3-core-data-model-networknode)
4. [LAN Authentication (Pairing)](#4-lan-authentication-pairing)
5. [PhilipsCondor Auth Scheme (Ongoing Request Auth)](#5-philipscondor-auth-scheme-ongoing-request-auth)
6. [LAN Communication Strategy](#6-lan-communication-strategy)
7. [LAN Request Execution](#7-lan-request-execution)
8. [Encryption Key Exchange](#8-encryption-key-exchange)
9. [AES Payload Encryption/Decryption](#9-aes-payload-encryptiondecryption)
10. [SSL/TLS and Certificate Pinning](#10-ssltls-and-certificate-pinning)
11. [LAN Transport Context](#11-lan-transport-context)
12. [Device Discovery (SSDP + mDNS)](#12-device-discovery-ssdp--mdns)
13. [UDP Event Subscriptions](#13-udp-event-subscriptions)
14. [Combined Communication Strategy](#14-combined-communication-strategy)
15. [HSDP Cloud Communication](#15-hsdp-cloud-communication)
16. [HSDP Remote Request Protocol](#16-hsdp-remote-request-protocol)
17. [HSDP Transport Context](#17-hsdp-transport-context)
18. [DaConnect Authentication Service](#18-daconnect-authentication-service)
19. [MQTT Connection Info](#19-mqtt-connection-info)
20. [Credential Storage (SQLite)](#20-credential-storage-sqlite)
21. [Database Schema Evolution](#21-database-schema-evolution)
22. [Crypto Utilities (ByteUtil)](#22-crypto-utilities-byteutil)
23. [Request/Response Framework](#23-requestresponse-framework)
24. [Appliance Model](#24-appliance-model)
25. [Appliance Manager](#25-appliance-manager)
26. [ApplianceFactory Interface](#26-appliancefactory-interface)
27. [Condor Entry Point (SDK Initialization)](#27-condor-entry-point-sdk-initialization)
28. [CondorPort (Port Abstraction Layer)](#28-condorport-port-abstraction-layer)
29. [SecurityPortProperties](#29-securityportproperties)
30. [DeviceCloudPairingPort](#30-devicecloudpairingport)
31. [HSDP Authentication](#31-hsdp-authentication)
32. [HSDP Configuration](#32-hsdp-configuration)
33. [HSDP Command Queue](#33-hsdp-command-queue)
34. [Error Codes (Complete List)](#34-error-codes-complete-list)
35. [ObservableCommunicationStrategy](#35-observablecommunicationstrategy)
36. [NullCommunicationStrategy](#36-nullcommunicationstrategy)
37. [TransportContext Interface](#37-transportcontext-interface)
38. [RuntimeConfiguration](#38-runtimeconfiguration)
39. [DeviceCache and CacheData](#39-devicecache-and-cachedata)
40. [Request Queue (Threading Model)](#40-request-queue-threading-model)
41. [Result Type (Success/Failure)](#41-result-type-successfailure)
42. [CondorPortApi Interface](#42-condorportapi-interface)
43. [PortSubscriptionListener](#43-portsubscriptionlistener)
44. [DevicePort and DevicePortProperties](#44-deviceport-and-deviceportproperties)
45. [HsdpPairingPort](#45-hsdppairingport)
46. [BinaryCondorPort](#46-binarycondorport)
47. [ConnectivityMonitor](#47-connectivitymonitor)
48. [Poller (Periodic Polling)](#48-poller-periodic-polling)
49. [AppIdProvider](#49-appidprovider)
50. [GsonProvider](#50-gsonprovider)
51. [SubscriptionEventListener and SubscriptionHandler](#51-subscriptioneventlistener-and-subscriptionhandler)
52. [DiscoveryStrategy Interface](#52-discoverystrategy-interface)
53. [ObservableDiscoveryStrategy](#53-observablediscoverystrategy)
54. [DiscoveredLanDevice](#54-discoveredlandevice)
55. [DiscoveredDeviceListener and DiscoveryMechanism](#55-discovereddevicelistener-and-discoverymechanism)
56. [UDP Event Receiver (Singleton)](#56-udp-event-receiver-singleton)
57. [UDP Receiving Thread](#57-udp-receiving-thread)
58. [SsidProvider](#58-ssidprovider)
59. [DatabaseHelper Interface](#59-databasehelper-interface)
60. [HSDP Error Enums](#60-hsdp-error-enums)
61. [HSDP Remote Subscription Handler](#61-hsdp-remote-subscription-handler)
62. [Condor Message Keys](#62-condor-message-keys)
63. [DaConnect Authentication Interfaces](#63-daconnect-authentication-interfaces)
64. [DaConnect Authentication Models](#64-daconnect-authentication-models)
65. [All Port Types Reference](#65-all-port-types-reference)
66. [HsdpPairingHandler (Full Pairing Flow)](#66-hsdppairinghandler-full-pairing-flow)
67. [HsdpPairingPortProperties](#67-hsdppairingportproperties)
68. [FirmwarePortProperties](#68-firmwareportproperties)
69. [HSDPController (Full)](#69-hsdpcontroller-full)
70. [HSDPControlPairingHandlerImpl](#70-hsdpcontrolpairinghandlerimpl)
71. [Remaining Port Properties and Utilities](#71-79-remaining-port-properties-and-utilities)
72. [Appendix A: Complete File Inventory](#appendix-a-complete-file-inventory)
73. [Appendix B: Verification Corrections](#appendix-b-verification-corrections)
74. [Appendix C: End-to-End Flows](#appendix-c-end-to-end-flows) (C.1-C.12j)
75. [Appendix D: JADX Obfuscation Rosetta Stone](#appendix-d-jadx-obfuscation-rosetta-stone)

---

## 0. Protocol Quick Start: Talk to a Device in 5 Minutes

If you just want to talk to a Philips airfryer/air purifier on your LAN, here's everything you need. No SDK, no Android, no cloud. Just raw HTTPS requests.

### Prerequisites

You need:
- Device IP address (find via mDNS `_philipscondor._tcp.local.` or SSDP `urn:philips-com:device:DiProduct:1`)
- `client_id`: 16 random bytes, base64-encoded (you generate this once)
- `client_secret`: base64 string (device gives you this during pairing)

### Step 1: Generate a client_id

```python
import base64, os
client_id = base64.b64encode(os.urandom(16)).decode()
# Example: "fTJdQStrzUhafYGycn7Jtw=="
```

### Step 2: Pair with the device

```
PUT https://{ip}/auth/v1/
Content-Type: application/json
(TLS: accept any certificate)

{"id": "fTJdQStrzUhafYGycn7Jtw=="}
```

**Response (new device):**
```json
{"authenticated": true, "secret": "abc123base64secret=="}
```
Save the `secret` - this is your `client_secret`. You only need to do this once.

**Response (already paired to another client):**
```json
{"authenticated": false, "seed": "someSeedValue"}
```
You need the existing `client_secret` to solve this challenge. If you don't have it, factory reset the device.

### Step 3: Make an authenticated request

Every request after pairing uses the `PHILIPS-Condor` challenge-response scheme.

**First request (no cached credentials):**
```
GET https://{ip}/di/v1/products/1/airfryer
Connection: keep-alive
(TLS: accept any certificate, or pin the public key SHA-256)
```

**Device responds with 401 + challenge:**
```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: PHILIPS-Condor tx7ShrFB7mlC26GQ/mSeIw==
```

### Step 4: Solve the challenge

```python
import base64, hashlib

# The challenge from WWW-Authenticate header (strip "PHILIPS-Condor " prefix)
challenge_b64 = "tx7ShrFB7mlC26GQ/mSeIw=="
challenge = base64.b64decode(challenge_b64)        # 16 bytes

client_id_bytes = base64.b64decode(client_id)       # 16 bytes
client_secret_bytes = base64.b64decode(client_secret) # 16 bytes

# SHA-256 of: challenge + client_id + client_secret
hash_input = challenge + client_id_bytes + client_secret_bytes  # 48 bytes
hash_output = hashlib.sha256(hash_input).digest()               # 32 bytes

# Response = client_id + hash
response = base64.b64encode(client_id_bytes + hash_output).decode()

# Authorization header
auth_header = f"PHILIPS-Condor {response}"
```

### Step 5: Retry with credentials

```
GET https://{ip}/di/v1/products/1/airfryer
Connection: keep-alive
Authorization: PHILIPS-Condor fTJdQStrzUhafYGycn7Jtxedal6/TLLSOsVgUFUo8HDf2UOZ4Bn82Wjw4XW2iELG
```

**Response (success):**
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

Cache the `Authorization` header value. Reuse it for all subsequent requests until you get another 401, then solve the new challenge.

### Step 6: Control the device

```
PUT https://{ip}/di/v1/products/1/airfryer
Content-Type: application/json
Authorization: PHILIPS-Condor <cached_credentials>

{"temp": 180, "time": 600, "status": "cooking"}
```

### Complete API Endpoint Reference

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/v1/` | PUT | Pair (body: `{"id":"..."}`) |
| `/di/v1/products/1/airfryer` | GET | Read airfryer state |
| `/di/v1/products/1/airfryer` | PUT | Set airfryer state |
| `/di/v1/products/1/status` | GET | Read air purifier state |
| `/di/v1/products/1/status` | PUT | Set air purifier state |
| `/di/v1/products/1/air` | GET | Read air quality data |
| `/di/v1/products/1/fltsts` | GET | Read filter status |
| `/di/v1/products/1/device` | GET | Read device info (name, model, firmware) |
| `/di/v1/products/0/security` | GET | Get AES encryption key |
| `/di/v1/products/0/firmware` | GET | Read firmware version info |
| `/di/v1/products/0/pairing` | ExecMethod | Cloud pairing RPC (via HSDP only) |

Other port names for specific devices: `venusaf` (HD9880), `venus1af` (HD9875/76), `nutrimax` (NX0960), `hermesac` (NX0950).

### TLS Configuration

- Protocol: TLSv1.2
- Certificate validation: Trust On First Use (TOFU). On first connect, store SHA-256 of server's public key. On subsequent connects, verify it matches.
- Hostname verification: disabled (devices use IP addresses)
- Cipher suites: `TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256`, `TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256`, `TLS_RSA_WITH_AES_128_CBC_SHA256`
- Or just: accept any certificate (the APK has a `createTLSTrustEverythingSSLContext` mode)

### AES Payload Decryption (HTTP devices only)

Some older devices (HTTP port 80, not HTTPS 443) encrypt their response bodies. Fetch the key first:

```
GET https://{ip}/di/v1/products/0/security
-> {"key": "abcdef0123456789abcdef0123456789"}
```

Then decrypt responses:

```python
import base64
from Crypto.Cipher import AES

key_hex = "abcdef0123456789abcdef0123456789"  # 32 hex chars = 16 bytes
key = bytes.fromhex(key_hex)
iv = b'\x00' * 16  # 16 zero bytes

def decrypt(encrypted_response: str) -> str:
    ciphertext = base64.b64decode(encrypted_response.strip())
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = cipher.decrypt(ciphertext)
    # Remove PKCS7 padding
    pad_len = plaintext[-1]
    plaintext = plaintext[:-pad_len]
    # Remove 2-byte random prefix
    plaintext = plaintext[2:]
    return plaintext.decode('utf-8')
```

### Base64 Encoding Details

All base64 in this protocol uses:
- **Encoding**: Android `Base64.NO_WRAP` (flag=2) = standard base64, no line breaks
- **Decoding**: Android `Base64.DEFAULT` (flag=0) = standard base64
- Equivalent to Python's `base64.b64encode()` / `base64.b64decode()`

### Wire Example: Complete Request/Response

```
--- REQUEST ---
PUT /auth/v1/ HTTP/1.1
Host: 192.168.1.100
Content-Type: application/json
Connection: keep-alive

{"id":"fTJdQStrzUhafYGycn7Jtw=="}

--- RESPONSE (new device) ---
HTTP/1.1 200 OK
Content-Type: application/json

{"authenticated":true,"secret":"Kx1eBqP8SvI6fYGycn7abc=="}

--- REQUEST ---
GET /di/v1/products/1/airfryer HTTP/1.1
Host: 192.168.1.100
Connection: keep-alive

--- RESPONSE (first request, no credentials) ---
HTTP/1.1 401 Unauthorized
WWW-Authenticate: PHILIPS-Condor tx7ShrFB7mlC26GQ/mSeIw==

--- REQUEST (retry with credentials) ---
GET /di/v1/products/1/airfryer HTTP/1.1
Host: 192.168.1.100
Connection: keep-alive
Authorization: PHILIPS-Condor fTJdQStrzUhafYGycn7Jtxedal6/TLLSOsVgUFUo8HDf2UOZ4Bn82Wjw4XW2iELG

--- RESPONSE ---
HTTP/1.1 200 OK
Content-Type: application/json

{"status":"standby","temp":0,"time":0,"cur_time":0,"drawer_open":false,"preset":0,"error":0}
```

### Property Merge Behavior

When the CondorPort receives new data (from polling or subscription), it **merges** with cached properties, not replaces.

```
Cached state:   {"temp": 180, "status": "cooking", "time": 600, "cur_time": 300}
Incoming event: {"cur_time": 295}
Merged result:  {"temp": 180, "status": "cooking", "time": 600, "cur_time": 295}
```

This means subscription push events can send partial updates containing only the changed fields.

### UDP Subscription Push Format

After subscribing (POST with `subscriber` + `ttl` + `changeudp`), the device sends UDP packets:

```
NOTIFY /di/v1/products/1/airfryer HTTP/1.1\r\n
HOST: 192.168.1.50:8080\r\n
\r\n
<base64_aes_encrypted_json>
```

The last line is the encrypted payload. Decrypt with the same AES key used for HTTP responses. The decrypted JSON is a partial update (merge with cached state).

---

## Appendix D: JADX Obfuscation Rosetta Stone

The decompiled code uses obfuscated names from ProGuard/R8. This table maps them to their real identities.

### OkHttp3 (package `ix0`)

| Obfuscated | Real Class | Purpose |
|------------|-----------|---------|
| `ix0.z` | `OkHttpClient` | HTTP client |
| `ix0.z.a` | `OkHttpClient.Builder` | Client builder |
| `ix0.b0` | `Request` | HTTP request |
| `ix0.b0.a` | `Request.Builder` | Request builder |
| `ix0.d0` | `Response` | HTTP response |
| `ix0.e0` | `ResponseBody` | Response body |
| `ix0.x` | `MediaType` | Content type |
| `ix0.c0` | `RequestBody` | Request body |
| `ix0.u` | `Headers` | Response headers |
| `ix0.p` | `Dispatcher` | Request dispatcher |
| `ix0.g0` | `ConnectionSpec` | TLS config |
| `ix0.i` | `CipherSuite` | TLS cipher suites |
| `ix0.l` | `ConnectionSpec.Builder` | TLS config builder |

### OkHttp3 Method Mapping

| Obfuscated Call | Real Method |
|----------------|-------------|
| `new b0.a().p(url)` | `new Request.Builder().url(url)` |
| `.q(url)` | `.url(URL)` |
| `.l(body)` | `.put(body)` (when used with `.i("PUT", body)`) |
| `.i(method, body)` | `.method(method, body)` |
| `.g(key, value)` | `.header(key, value)` |
| `.a(key, value)` | `.addHeader(key, value)` |
| `.b()` | `.build()` |
| `client.a(request)` | `client.newCall(request)` |
| `client.y()` | `client.newBuilder()` |
| `builder.R(t, u)` | `.readTimeout(t, u)` |
| `builder.g(t, u)` | `.connectTimeout(t, u)` |
| `builder.U(t, u)` | `.writeTimeout(t, u)` |
| `builder.e(t, u)` | `.callTimeout(t, u)` |
| `builder.S(sf)` | `.socketFactory(sf)` |
| `builder.T(sf, tm)` | `.sslSocketFactory(sf, tm)` |
| `builder.O(hv)` | `.hostnameVerifier(hv)` |
| `builder.i(specs)` | `.connectionSpecs(specs)` |
| `builder.c()` | `.build()` |
| `response.getCode()` | `.code()` |
| `response.getHeaders()` | `.headers()` |
| `response.getBody()` | `.body()` |
| `headers.c(name)` | `.get(name)` |
| `x.INSTANCE.a(type)` | `MediaType.parse(type)` |
| `c0.INSTANCE.h(str, mt)` | `RequestBody.create(str, mt)` |
| `dispatcher.a()` | `.cancelAll()` |

### Kotlin Standard Library (package `kotlin.jvm.internal`)

| Obfuscated | Purpose |
|------------|---------|
| `s.j(obj, name)` | `Intrinsics.checkNotNullParameter(obj, name)` - null check |
| `s.i(obj, msg)` | `Intrinsics.checkNotNullExpressionValue(obj, msg)` |
| `s.h(obj, msg)` | `Intrinsics.checkNotNull(obj, msg)` - cast check |
| `s.g(obj)` | `Intrinsics.checkNotNull(obj)` |
| `s.e(a, b)` | `Intrinsics.areEqual(a, b)` |
| `s.l(a, b)` | `Intrinsics.compare(a, b)` |
| `s.A(name)` | `Intrinsics.throwUninitializedPropertyAccessException(name)` |
| `t0.f93555a` | `Unit.INSTANCE` |
| `u0.d(map)` | `MapsKt.toMutableMap(map)` |

### Logging (package `gw`)

| Obfuscated | Purpose |
|------------|---------|
| `gw.c.INSTANCE` | Logger singleton |
| `c.Companion.e(inst, lib, tag, msg, ...)` | `Logger.warn(tag, msg)` |
| `c.b(lib, tag, msg)` | `Logger.verbose(tag, msg)` |
| `c.c(lib, tag, msg)` | `Logger.error(tag, msg)` |
| `c.e(lib, tag, msg)` | `Logger.debug(tag, msg)` |
| `c.f(lib, tag, msg)` | `Logger.info(tag, msg)` |
| `c.g(lib, tag, msg)` | `Logger.debug(tag, msg)` (alternate) |
| `c.h(lib, tag, msg)` | `Logger.warn(tag, msg)` (alternate) |
| `c.i(lib, tag, msg)` | `Logger.verbose(tag, msg)` (alternate) |
| `c.k(lib, tag, msg)` | `Logger.warn(tag, msg)` (alternate) |

### Regex (package `vv0`)

| Obfuscated | Purpose |
|------------|---------|
| `new o(pattern)` | `new Regex(pattern)` |
| `.j(input, replacement)` | `.replace(input, replacement)` |
| `f0.c0(str, substr, ...)` | `StringsKt.contains(str, substr, ...)` |
| `f0.b0(str, char, ...)` | `StringsKt.contains(str, char, ...)` |
| `c0.G(str, suffix, ...)` | `StringsKt.endsWith(str, suffix, ...)` |

### Collections (packages `hs0`, `gs0`, `ns0`)

| Obfuscated | Purpose |
|------------|---------|
| `t0.i()` | `Collections.emptyMap()` |
| `t0.l(pair1, pair2, ...)` | `mapOf(pair1, pair2, ...)` |
| `s0.f(pair)` | `Collections.singletonMap(pair)` |
| `a0.a(key, value)` | `Pair(key, value)` / `Map.entry(key, value)` |
| `ns0.a` | `EnumEntries` |
| `ns0.b.a(values)` | `EnumEntries.of(values)` |

### Firebase Constants

| Obfuscated | Real Value |
|------------|-----------|
| `Constants.IPC_BUNDLE_KEY_SEND_ERROR` | `"error"` |
| `RemoteSettings.FORWARD_SLASH_STRING` | `"/"` |
| `Headers.CONN_DIRECTIVE` | `"Connection"` |
| `ClientCookie.PATH_ATTR` | `"path"` |
| `ClientCookie.PORT_ATTR` | `"port"` |
| `ClientCookie.VERSION_ATTR` | `"version"` |

### Android Base64 Flags

| Flag | Value | Meaning |
|------|-------|---------|
| `Base64.DEFAULT` | 0 | Standard base64 with line breaks (decode accepts any) |
| `Base64.NO_WRAP` | 2 | No line breaks in output |
| `Base64.NO_PADDING` | 1 | Omit trailing `=` padding |

The APK uses flag 2 (NO_WRAP) for encoding and flag 0 (DEFAULT) for decoding. Python's `base64.b64encode()`/`base64.b64decode()` are equivalent.

---

## 1. Architecture Overview

The APK has a layered architecture:

```
+-----------------------------------------------------------+
|                    OneKA App Layer                          |
|  (ka.oneka.app - UI, ViewModels, Fragments, DI)           |
+-----------------------------------------------------------+
|                   DaConnect SDK                             |
|  (cl.daconnect - Cloud API, Auth, Device Control, MQTT)    |
+-----------------------------------------------------------+
|                  Condor SDK (Core)                          |
|  (connectivity.condor.core - Appliance, NetworkNode,       |
|   CommunicationStrategy, Discovery, Store, Security)       |
+-----------------------------------------------------------+
|         LAN Transport          |    HSDP Transport         |
| (condor.lan - HTTPS, Auth,    | (condor.hsdp - MQTT,      |
|  Discovery, Crypto, SSL)      |  Controller, Messages)     |
+-----------------------------------------------------------+
|                   HSDP Client Library                      |
| (connectivity.hsdpclient - Generated API, MQTT service)    |
+-----------------------------------------------------------+
```

**Communication Strategy Pattern:** The app uses a strategy pattern where `CombinedCommunicationStrategy` picks the first available transport (LAN preferred, HSDP cloud fallback). Both transports implement the same `CommunicationStrategy` interface (getProperties, putProperties, subscribe, etc.).

---

## 2. Package Structure

| Package | Purpose | File Count |
|---------|---------|-----------|
| `connectivity.condor.core` | Core framework: NetworkNode, store, security, request queue | ~102 |
| `connectivity.condor.lan` | LAN transport: HTTPS, PhilipsCondor auth, discovery, crypto | ~32 |
| `connectivity.condor.hsdp` | HSDP cloud transport: MQTT relay, messages | ~19 |
| `connectivity.hsdpclient` | HSDP API client: generated code, MQTT service | ~657 |
| `cl.daconnect` | DaConnect SDK: cloud auth, device control, IoT API | ~269 |
| `ka.oneka` | App layer: UI, domain, backend, database | ~14,000+ |

---

## 3. Core Data Model: NetworkNode

**File:** `connectivity/condor/core/networknode/NetworkNode.java`

The `NetworkNode` is the central data model representing a discovered/paired device. Every field is synchronized and fires `PropertyChangeEvent` on mutation.

```java
// --- NetworkNode.java (annotated) ---

public class NetworkNode implements Parcelable {

    // Protocol version for the Condor API - always 1
    private static final int CONDOR_PROTOCOL_VERSION = 1;

    // --- Database column key constants ---
    public static final String KEY_BOOT_ID = "bootid";           // Device boot counter
    public static final String KEY_CLIENT_ID = "client_id";      // Our locally-generated auth ID (base64, 128-bit random)
    public static final String KEY_CLIENT_SECRET = "client_secret"; // Secret returned by device during pairing
    public static final String KEY_CPP_ID = "cppid";             // Cloud PP ID - unique device identifier
    public static final String KEY_DEVICE_NAME = "dev_name";     // Friendly name from discovery
    public static final String KEY_DEVICE_TYPE = "device_type";  // Model name (e.g., "AC2729")
    public static final String KEY_ENCRYPTION_KEY = "encryption_key"; // AES-128 key (hex string) for payload encryption
    public static final String KEY_HSDP_ID = "hsdpid";           // HSDP device ID for cloud relay
    public static final String KEY_HTTPS = "https";              // Whether device uses HTTPS (always true in schema)
    public static final String KEY_ID = "_id";                   // SQLite row ID (primary key)
    public static final String KEY_IP_ADDRESS = "ip_address";    // LAN IP address
    public static final String KEY_IS_PAIRED = "is_paired";      // Pairing state ordinal
    public static final String KEY_LAST_KNOWN_NETWORK = "lastknown_network"; // WiFi SSID when last seen
    public static final String KEY_LAST_PAIRED = "last_paired";  // Timestamp of last pairing
    public static final String KEY_MAC_ADDRESS = "mac_address";  // Device MAC
    public static final String KEY_MISMATCHED_PIN = "mismatched_pin"; // Certificate pin that didn't match stored pin
    public static final String KEY_MODEL_ID = "model_id";        // Model number from discovery
    @Deprecated public static final String KEY_MODEL_NAME = "model_name"; // Deprecated: old model name column
    public static final String KEY_PIN = "pin";                  // SHA-256 hash of device's TLS public key (base64)

    // --- Instance fields ---
    private long bootId;                // Device boot counter - if changed, encryption key is invalidated
    private String clientId;            // Our auth client ID
    private String clientSecret;        // Device-issued secret
    private String cppId;               // Unique device identifier
    private String credentials;         // Cached HTTP Authorization header value (PHILIPS-Condor scheme)
    private String deviceType;          // Model name
    private String encryptionKey;       // AES-128 key as hex string
    private long expirationPeriodMillis; // Default: 15 seconds - discovery cache expiration
    private String hsdpId;              // HSDP cloud device ID
    private String ipAddress;           // LAN IP
    private long lastPairedTime;        // Timestamp
    private String macAddress;          // MAC address
    private String mismatchedPin;       // New pin when device cert changes
    private String modelId;             // Model number
    private String name;                // Friendly name
    private String networkSsid;         // WiFi SSID
    private PairingState pairedState;   // PAIRED, NOT_PAIRED, UNPAIRED, PAIRING
    private final PropertyChangeSupport pcs; // Fires events when any field changes
    private String pin;                 // Stored TLS certificate pin

    // Pairing states
    public enum PairingState {
        PAIRED,       // ordinal 0 - fully paired, has client_id + client_secret
        NOT_PAIRED,   // ordinal 1 - not yet paired
        UNPAIRED,     // ordinal 2 - was paired, now unpaired
        PAIRING       // ordinal 3 - pairing in progress
    }

    // SecurityInfo bundles all security-related fields for transfer
    public static class SecurityInfo {
        String clientId;
        String clientSecret;
        String credentials;
        String mismatchedPin;
        String pin;
    }

    public NetworkNode() {
        this.pcs = new PropertyChangeSupport(this);
        this.expirationPeriodMillis = TimeUnit.SECONDS.toMillis(15L); // 15s default discovery TTL
        this.pairedState = PairingState.NOT_PAIRED;
    }

    // Condor API version is hardcoded to 1
    public int getCondorProtocolVersion() { return 1; }

    // A NetworkNode is valid if it has cppId, name, deviceType, and optionally a valid IP
    public boolean isValid() {
        boolean hasRequired = !TextUtils.isEmpty(getCppId())
            && !TextUtils.isEmpty(getName())
            && !TextUtils.isEmpty(getDeviceType());
        if (!TextUtils.isEmpty(getIpAddress())) {
            return hasRequired & Patterns.IP_ADDRESS.matcher(getIpAddress()).matches();
        }
        return hasRequired;
    }

    // When updating from a newly-discovered node:
    // - If bootId changed (device rebooted), encryption key is invalidated
    // - hsdpId is only updated if the new value is non-null
    public void updateWithValuesFrom(NetworkNode other) {
        if (Objects.equals(other.getCppId(), this.cppId)) {
            // Update SSID, IP, name, modelId, deviceType if changed
            if (other.getBootId() != this.bootId && other.getBootId() != -1) {
                setEncryptionKey(null);    // Invalidate encryption key on reboot
                setBootId(other.getBootId());
            }
            if (other.getEncryptionKey() != null) {
                setEncryptionKey(null);    // Also invalidate if new node has a key (force re-fetch)
            }
            if (!Objects.equals(other.getHsdpId(), this.hsdpId) && other.getHsdpId() != null) {
                setHsdpId(other.getHsdpId());
            }
        }
    }
}
```

**Key takeaways:**
- `cppId` is the universal device identifier (used in SQLite, cloud API, LAN discovery)
- `clientId` is generated locally (128-bit random, base64) and sent to device during pairing
- `clientSecret` comes from the device during pairing (stored for ongoing PhilipsCondor auth)
- `encryptionKey` is fetched separately via `/di/v1/products/0/security` (hex string, AES-128)
- `pin` is SHA-256 of the device's TLS public key for certificate pinning
- `credentials` is the cached `Authorization` header value (regenerated on 401)

---

## 4. LAN Authentication (Pairing)

**File:** `connectivity/condor/lan/authentication/Authentication.java`

This class handles the initial device pairing flow at `PUT /auth/v{version}/`.

```java
// --- Authentication.java (annotated) ---

public class Authentication {

    // URL template: https://{ip}/auth/v{version}/
    private static final String BASEURL_AUTH_HTTPS = "https://%s/auth/v%d/";

    // Error constants returned via callback
    private static final String ERROR_BAD_GATEWAY = "error_bad_gateway";
    private static final String ERROR_BAD_REQUEST = "error_bad_request";
    private static final String ERROR_TIMEOUT = "error_timeout";

    // JSON response keys
    private static final String KEY_AUTHENTICATED = "authenticated"; // boolean: true = pairing succeeded
    private static final String KEY_SECRET = "secret";               // string: the client_secret

    private final ConnectivityMonitor connectivityMonitor; // Checks WiFi availability
    private final LanTransportContext lanTransportContext; // Creates OkHttp clients
    private Handler requestHandler;   // Background thread for HTTP requests
    private final Handler responseHandler; // Main thread for callbacks

    public Authentication(ConnectivityMonitor connectivityMonitor, LanTransportContext lanTransportContext) {
        // Create a dedicated HandlerThread for authentication requests
        // so they don't block the main thread
        new HandlerThread("AuthenticationHandlerThread") {
            @Override
            public void onLooperPrepared() {
                // Set the request handler once the looper is ready
                Authentication.this.setRequestHandler(HandlerProvider.createHandler(getLooper()));
                super.onLooperPrepared();
            }
        }.start();
        // Response callbacks go on main thread
        this.responseHandler = HandlerProvider.createHandler();
    }

    // Generate or retrieve the client ID for this device
    private String initNetworkNodeClientID(NetworkNode networkNode) {
        String clientId = networkNode.getClientId();
        if (clientId == null || clientId.length() == 0) {
            // First time: generate a random 128-bit key, base64-encoded
            clientId = ByteUtil.create128bitBase64EncodedKey();
            networkNode.setClientId(clientId);
        }
        return clientId;
    }

    // Public entry point: authenticate/pair with a device
    // evidence = additional key-value pairs (e.g., seed challenge response)
    public void authenticate(NetworkNode networkNode, Map<String, Object> evidence,
                           AuthenticationCallback authenticationCallback) {
        // Post the work to the background handler thread
        this.requestHandler.post(() -> authenticate$lambda$2(networkNode, this, evidence, authenticationCallback));
    }

    // The actual HTTP request logic (runs on background thread)
    private static void authenticate$lambda$2(NetworkNode networkNode, Authentication self,
                                              Map evidence, AuthenticationCallback callback) {
        // Build the URL: https://{ip}/auth/v{version}/
        String url = String.format(Locale.US, BASEURL_AUTH_HTTPS,
            networkNode.getIpAddress(), networkNode.getCondorProtocolVersion());

        try {
            // Check WiFi is available
            if (!self.connectivityMonitor.isAvailable()) {
                throw new TransportUnavailableException("Network unavailable.");
            }

            // Build JSON body: always includes "id" (our client_id)
            // If evidence map is provided (e.g., challenge response), merge it in
            HashMap requestParams = new HashMap();
            requestParams.put("id", self.initNetworkNodeClientID(networkNode));
            if (evidence != null) {
                requestParams.putAll(evidence);
            }

            // Create a PUT request with JSON body
            // HTTP method is PUT (confirmed from APK deobfuscation: .l(body) -> .i("PUT", body))
            Request request = new Request.Builder()
                .url(url)
                .method("PUT", RequestBody.create(
                    self.createRequestPayload(requestParams),
                    MediaType.parse("application/json")))
                .build();

            // Get or create OkHttp client (with TLS, cert pinning, LAN-only network)
            OkHttpClient client = self.lanTransportContext
                .createOrGetOkHttpClient(networkNode);
            if (client == null) {
                throw new TransportUnavailableException("Network unavailable.");
            }

            // Execute the HTTP request synchronously (we're on a background thread)
            Response response = client.newCall(request).execute();

            // Get response code
            int statusCode;
            try {
                statusCode = response.code();
            } catch (SocketTimeoutException e) {
                statusCode = 504; // Gateway Timeout
            } catch (Exception e) {
                statusCode = 502; // Bad Gateway
            }

            // Read response body
            String bodyString = null;
            if (response.body() != null) {
                bodyString = response.body().string();
            }

            // Handle response based on status code
            if (statusCode == 200) {
                // SUCCESS - Parse JSON response
                Map<String, Object> responseMap = new Gson().fromJson(bodyString, HashMap.class);

                // Extract "authenticated" boolean
                boolean authenticated = false;
                if (responseMap.containsKey(KEY_AUTHENTICATED)) {
                    authenticated = (Boolean) responseMap.get(KEY_AUTHENTICATED);
                    responseMap.remove(KEY_AUTHENTICATED); // Remove from map before passing to callback
                }

                // Extract "secret" string
                if (responseMap.containsKey(KEY_SECRET)) {
                    String secret = (String) responseMap.get(KEY_SECRET);
                    responseMap.remove(KEY_SECRET);
                    // Only store secret if authenticated=true AND secret is non-null
                    if (authenticated && secret != null) {
                        networkNode.setClientSecret(secret);
                    }
                }

                // Fire callback with: authenticated flag, remaining response fields
                // Remaining fields may include "seed" for challenge-response flow
                self.sendCallback(callback, authenticated, responseMap);

            } else if (statusCode == 400) {
                self.sendCallback(callback, new AuthenticationError(ERROR_BAD_REQUEST));
            } else if (statusCode == 502) {
                self.sendCallback(callback, new AuthenticationError(ERROR_BAD_GATEWAY));
            } else {
                self.sendCallback(callback, new AuthenticationError("Request failed - " + bodyString));
            }

        } catch (TransportUnavailableException e) {
            self.sendCallback(callback, new AuthenticationError("Request failed - no wifi connection available"));
        } catch (SSLHandshakeException e) {
            self.sendCallback(callback, new AuthenticationError(e.getMessage()));
        } catch (IOException e) {
            self.sendCallback(callback, new AuthenticationError(e.getMessage()));
        }
    }

    // Convert map to JSON string
    private String createRequestPayload(Map<String, Object> params) {
        return GsonProvider.get().toJson(params, Map.class);
    }

    // Post success callback to main thread
    private void sendCallback(AuthenticationCallback callback, boolean authenticated, Map<String, Object> response) {
        this.responseHandler.post(() -> callback.response(authenticated, response, null));
    }

    // Post error callback to main thread
    private void sendCallback(AuthenticationCallback callback, AuthenticationError error) {
        this.responseHandler.post(() -> callback.response(false, null, error));
    }
}
```

**Pairing Protocol (two patterns):**

1. **Direct pairing (new device):**
   - `PUT /auth/v1/` with body `{"id": "<client_id>"}`
   - Response: `{"authenticated": true, "secret": "<client_secret>"}`
   - App stores client_secret in NetworkNode

2. **Seed challenge (already-paired device):**
   - `PUT /auth/v1/` with body `{"id": "<client_id>"}`
   - Response: `{"authenticated": false, "seed": "<seed_value>"}`
   - App computes `key = SHA256(seed + client_id)` and retries:
   - `PUT /auth/v1/` with body `{"id": "<client_id>", "key": "<evidence>"}`
   - Response: `{"authenticated": true, "secret": "<new_secret>"}`

---

## 5. PhilipsCondor Auth Scheme (Ongoing Request Auth)

**File:** `connectivity/condor/lan/authentication/PhilipsCondorScheme.java`
**File:** `connectivity/condor/lan/communication/LanRequest.java` (method: `createCredentialsFrom`)

After pairing, every LAN request uses the `PHILIPS-Condor` HTTP auth scheme for ongoing authorization.

```java
// --- PhilipsCondorScheme.java ---
// Simply identifies the auth scheme name
public class PhilipsCondorScheme implements Scheme {
    @Override
    public String getSchemeIdentifier() {
        return "PHILIPS-Condor"; // The WWW-Authenticate scheme identifier
    }
}

// --- LanRequest.java: createCredentialsFrom() ---
// This method is called when a request returns HTTP 401 with a
// WWW-Authenticate: PHILIPS-Condor <base64_challenge> header

public String createCredentialsFrom(String base64EncodedChallenge) {
    // Step 1: Strip the scheme prefix to get the raw base64 challenge
    // Regex: (?i)PHILIPS-Condor  (case-insensitive match + strip)
    byte[] challenge = ByteUtil.decodeFromBase64(
        base64EncodedChallenge.replaceAll("(?i)PHILIPS-Condor ", ""));

    // Step 2: Validate challenge is exactly 16 bytes
    if (challenge.length != 16) return null;

    // Step 3: Check we have valid client_id and client_secret
    String clientId = this.networkNode.getClientId();
    String clientSecret = this.networkNode.getClientSecret();
    if (clientId == null || clientId.isEmpty()) return null;
    if (clientSecret == null || clientSecret.isEmpty()) return null;

    // Step 4: Decode client_id and client_secret from base64 to raw bytes
    byte[] clientIdBytes = ByteUtil.decodeFromBase64(clientId);
    byte[] clientSecretBytes = ByteUtil.decodeFromBase64(clientSecret);

    // Step 5: Concatenate: challenge + clientId + clientSecret
    byte[] toHash = ByteUtil.concatenate(challenge, clientIdBytes, clientSecretBytes);

    // Step 6: SHA-256 hash of the concatenation
    byte[] hash = ByteUtil.createSHA256HashFrom(toHash);
    if (hash == null) return null;

    // Step 7: Concatenate: clientId + hash
    byte[] credentials = ByteUtil.concatenate(clientIdBytes, hash);

    // Step 8: Base64 encode and prepend scheme identifier
    return "PHILIPS-Condor " + ByteUtil.encodeToBase64(credentials);
    // Result: "PHILIPS-Condor <base64(clientId + SHA256(challenge + clientId + clientSecret))>"
}
```

**Challenge-Response Flow:**
```
Client -> Device:  GET /di/v1/products/1/...
                   Authorization: PHILIPS-Condor <cached_credentials>

Device -> Client:  HTTP 401
                   WWW-Authenticate: PHILIPS-Condor <base64(16_random_bytes)>

Client computes:   response = base64(clientId_bytes + SHA256(challenge + clientId_bytes + clientSecret_bytes))

Client -> Device:  GET /di/v1/products/1/...  (retry same request)
                   Authorization: PHILIPS-Condor <response>

Device -> Client:  HTTP 200 (success)
```

The computed `Authorization` header value is cached in `NetworkNode.credentials` and reused until the next 401.

---

## 6. LAN Communication Strategy

**File:** `connectivity/condor/lan/communication/LanCommunicationStrategy.java`

This is the main LAN transport implementation.

```java
// --- LanCommunicationStrategy.java (annotated) ---

public class LanCommunicationStrategy extends ObservableCommunicationStrategy {

    private static final int TTL_DEFAULT_IN_SECS = 300; // 5 minute subscription TTL

    private final ConnectivityMonitor connectivityMonitor; // WiFi availability monitor
    private final Crypto crypto;                          // AES decryption for encrypted responses
    private boolean isAvailable;                          // Cached availability state
    private boolean isKeyExchangeOngoing;                 // Prevents concurrent key fetches
    private final LanTransportContext lanTransportContext; // HTTP client factory
    private final LocalSubscriptionHandler localSubscriptionHandler; // UDP event handler
    private final NetworkNode networkNode;                // The device
    private final RequestQueue requestQueue;              // Serial request execution
    private final SsidProvider ssidProvider;              // Current WiFi SSID
    private int subscriptionTtl = 300;                    // 5 minutes

    public LanCommunicationStrategy(NetworkNode networkNode, ConnectivityMonitor connectivityMonitor,
                                     SsidProvider ssidProvider, LanTransportContext lanTransportContext) {
        this.networkNode = networkNode;
        this.connectivityMonitor = connectivityMonitor;
        this.ssidProvider = ssidProvider;
        this.lanTransportContext = lanTransportContext;

        // Create crypto handler for this device (uses its encryption_key)
        Crypto crypto = new Crypto(networkNode);
        this.crypto = crypto;

        // Create local subscription handler (UDP events + decryption)
        this.localSubscriptionHandler = new LocalSubscriptionHandler(crypto, UdpEventReceiver.getInstance());

        // Listen for WiFi availability changes
        connectivityMonitor.addAvailabilityListener(availability -> handleAvailabilityChanged());

        // Listen for NetworkNode property changes (e.g., IP address change)
        networkNode.addPropertyChangeListener(event -> handleAvailabilityChanged());

        // Listen for WiFi network changes (SSID changes)
        ssidProvider.addNetworkChangeListener(() -> handleAvailabilityChanged());

        // Create the serial request queue
        this.requestQueue = new RequestQueue();

        // When decryption fails, trigger a key re-exchange
        crypto.setListener(nn -> triggerKeyExchange(nn));

        // Cache initial availability
        this.isAvailable = isAvailable();
    }

    // Availability check: needs IP + WiFi + same network
    @Override
    public boolean isAvailable() {
        return networkNode.getIpAddress() != null
            && connectivityMonitor.isAvailable()
            && isOnSameNetwork();
    }

    // Check if we're on the same WiFi network as the device
    private boolean isOnSameNetwork() {
        String currentSsid = ssidProvider.getCurrentSsid();
        // If we don't know the current SSID, assume same network
        if (currentSsid != null && !currentSsid.equals(networkNode.getNetworkSsid())) {
            return false;
        }
        return true;
    }

    // GET properties from device
    @Override
    public void getProperties(String portName, int productId, ResponseHandler handler) {
        exchangeKeyIfNecessary(networkNode); // Ensure we have encryption key
        requestQueue.addRequest(createUnauthorizedHandlingRequest(
            portName, productId, LanRequestType.GET, null, handler));
    }

    // PUT properties to device
    @Override
    public void putProperties(Map<String, Object> data, String portName, int productId, ResponseHandler handler) {
        exchangeKeyIfNecessary(networkNode);
        requestQueue.addRequest(createUnauthorizedHandlingRequest(
            portName, productId, LanRequestType.PUT, data, handler));
    }

    // POST properties (add)
    @Override
    public void addProperties(Map<String, Object> data, String portName, int productId, ResponseHandler handler) {
        exchangeKeyIfNecessary(networkNode);
        requestQueue.addRequest(createUnauthorizedHandlingRequest(
            portName, productId, LanRequestType.POST, data, handler));
    }

    // DELETE properties
    @Override
    public void deleteProperties(String portName, int productId, ResponseHandler handler) {
        exchangeKeyIfNecessary(networkNode);
        requestQueue.addRequest(createUnauthorizedHandlingRequest(
            portName, productId, LanRequestType.DELETE, null, handler));
    }

    // Wrapper that auto-retries on 401 Unauthorized
    private Request createUnauthorizedHandlingRequest(String portName, int productId,
            LanRequestType type, Map<String, Object> data, ResponseHandler handler) {
        return createRequest(portName, productId, type, data, new ResponseHandler() {
            @Override
            public void onError(Error error, String message) {
                if (error == Error.REQUEST_UNAUTHORIZED) {
                    // 401: credentials were refreshed by the failed request,
                    // retry the same request (now with new credentials)
                    requestQueue.addRequest(createRequest(portName, productId, type, data, handler));
                } else {
                    handler.onError(error, message);
                }
            }
            @Override
            public void onSuccess(byte[] data) { handler.onSuccess(data); }
        });
    }

    // Fetch encryption key if we don't have one
    private void exchangeKeyIfNecessary(NetworkNode node) {
        if (node.getEncryptionKey() == null && !isKeyExchangeOngoing) {
            doKeyExchange(node);
        }
    }

    // Fetch encryption key from device's security port
    private void doKeyExchange(NetworkNode node) {
        GetKeyRequest request = new GetKeyRequest(node, connectivityMonitor, lanTransportContext,
            new ResponseHandler() {
                @Override
                public void onSuccess(byte[] data) {
                    // Store the encryption key (UTF-8 hex string)
                    node.setEncryptionKey(new String(data, StandardCharsets.UTF_8));
                    isKeyExchangeOngoing = false;
                }
                @Override
                public void onError(Error error, String msg) {
                    isKeyExchangeOngoing = false;
                }
            });
        isKeyExchangeOngoing = true;
        requestQueue.addRequestInFrontOfQueue(request); // Priority: key exchange goes first
    }

    // When decryption fails, reset key and re-fetch
    private void triggerKeyExchange(NetworkNode node) {
        node.setEncryptionKey(null);
        exchangeKeyIfNecessary(node);
    }
}
```

---

## 7. LAN Request Execution

**File:** `connectivity/condor/lan/communication/LanRequest.java`

Each LAN request is an HTTP call to `https://{ip}/di/v{version}/products/{productId}/{portName}`.

```java
// --- LanRequest.java (annotated) ---

public class LanRequest extends Request {

    private static final int CHALLENGE_SIZE = 16; // PhilipsCondor challenge is 16 bytes
    public static final String HEADER_AUTHORIZATION = "Authorization";
    public static final String HEADER_CHALLENGE = "WWW-Authenticate";
    public static final int HTTP_TOO_MANY_REQUESTS = 429;

    // Error mapping: response body error strings to Error enum
    private static final Map<String, Error> errorMap; // {"out of memory" -> OUT_OF_MEMORY}
    private static final PhilipsCondorScheme scheme = new PhilipsCondorScheme();

    // Request parameters
    private final NetworkNode networkNode;
    private final ConnectivityMonitor connectivityMonitor;
    private final LanTransportContext lanTransportContext;
    private final String portName;     // e.g., "air", "status", "security"
    private final int productId;       // 0 or 1
    private final LanRequestType requestType; // GET, PUT, POST, DELETE

    // HTTP request types map to standard HTTP methods
    // LanRequestType enum: POST("POST"), DELETE("DELETE"), PUT("PUT"), GET("GET")

    // Build the URL for this request
    public URL createURL() throws MalformedURLException {
        // Format: https://{ip}/di/v{version}/products/{productId}/{portName}
        return new URL("https://" + networkNode.getIpAddress()
            + "/di/v" + networkNode.getCondorProtocolVersion()
            + "/products/" + productId + "/" + portName);
    }

    // Build the OkHttp request builder with keep-alive
    public Request.Builder createRequestBuilder() {
        return new Request.Builder()
            .url(createURL())
            .header("Connection", "keep-alive");
    }

    @Override
    public Response execute() {
        try {
            // Build request
            Request.Builder builder = createRequestBuilder();

            // Add cached credentials if we have them
            String credentials = networkNode.getCredentials();
            if (credentials != null) {
                builder.header("Authorization", credentials);
            }

            // Add request body for PUT/POST/DELETE (JSON)
            // For GET with no data, or PUT/POST with empty data: skip body
            if ((requestType != PUT && requestType != POST) || (dataMap != null && !dataMap.isEmpty())) {
                RequestBody body = null;
                if (requestType == PUT || requestType == POST || requestType == DELETE) {
                    body = RequestBody.create(
                        createRequestPayload(dataMap),
                        MediaType.parse("application/json"));
                }
                builder.method(requestType.getMethod(), body);
            } else {
                // PUT/POST with no data: error
                return new Response(null, Error.NO_REQUEST_DATA);
            }

            // Get OkHttp client (with TLS, cert pinning)
            OkHttpClient client = lanTransportContext.createOrGetOkHttpClient(networkNode);
            if (client == null) {
                return new Response("Network Unavailable.", Error.NO_TRANSPORT_AVAILABLE);
            }

            // Reset timeouts to 30s each
            resetClientTimeout(client);

            // Execute HTTP request
            okhttp3.Response httpResponse = client.newCall(builder.build()).execute();
            int code = httpResponse.code();
            Headers headers = httpResponse.headers();
            ResponseBody body = httpResponse.body();

            if (body == null) {
                return new Response(null, Error.REQUEST_FAILED);
            }
            String bodyString = body.string();

            // Handle response codes
            switch (code) {
                case 200: return handleHttpOk(headers, bodyString);
                case 400: return handleBadRequest(bodyString);
                case 401: return handleUnauthorized(headers, bodyString);
                case 429: return new Response(null, Error.BUSY); // Rate limited
                case 502: return new Response(null, Error.CANNOT_CONNECT);
                default:
                    Error errorFromBody = findErrorInResponseBody(bodyString);
                    return new Response(bodyString,
                        errorFromBody != null ? errorFromBody : Error.REQUEST_FAILED);
            }

        } catch (SSLHandshakeException e) {
            return new Response(null, Error.INSECURE_CONNECTION);
        } catch (IOException e) {
            return new Response(null, Error.IOEXCEPTION);
        }
    }

    // Handle HTTP 200 OK
    public Response handleHttpOk(Headers headers, String body) {
        if (body.isEmpty()) {
            return new Response(null, Error.EMPTY_RESPONSE);
        }
        return new Response(body, null); // Success!
    }

    // Handle HTTP 401 Unauthorized
    public Response handleUnauthorized(Headers headers, String body) {
        // Clear cached credentials
        networkNode.setCredentials(null);

        // Extract challenge from WWW-Authenticate header
        String challenge = headers.get("WWW-Authenticate");
        if (challenge != null) {
            // Compute new credentials from challenge
            networkNode.setCredentials(createCredentialsFrom(challenge));
        }

        return new Response(body, Error.REQUEST_UNAUTHORIZED);
        // Caller (createUnauthorizedHandlingRequest) will retry with new credentials
    }

    // Handle HTTP 400 Bad Request
    public Response handleBadRequest(String body) {
        return new Response(body, Error.NOT_UNDERSTOOD);
    }

    // Convert data map to JSON, encoding byte arrays to base64
    private String createRequestPayload(Map<String, Object> dataMap) {
        if (dataMap != null && !dataMap.isEmpty()) {
            Map<String, Object> encoded = ByteUtil.encodeByteArraysToBase64(dataMap);
            return GsonProvider.get().toJson(encoded, Map.class);
        }
        return "{}";
    }

    // Reset OkHttp client timeouts to 30ms
    // (decompiled code literally says TimeUnit.MILLISECONDS with value 30;
    //  likely a decompilation artifact or app bug - real intent probably 30s)
    private void resetClientTimeout(OkHttpClient client) {
        client.newBuilder()
            .readTimeout(30L, TimeUnit.MILLISECONDS)
            .connectTimeout(30L, TimeUnit.MILLISECONDS)
            .writeTimeout(30L, TimeUnit.MILLISECONDS)
            .callTimeout(30L, TimeUnit.MILLISECONDS)
            .build();
    }
}
```

---

## 8. Encryption Key Exchange

**File:** `connectivity/condor/lan/communication/GetKeyRequest.java`

The encryption key is fetched from the device's security port.

```java
// --- GetKeyRequest.java (annotated) ---

public class GetKeyRequest extends LanRequest {

    private static final String SECURITY_PORT_NAME = "security"; // Port name in URL
    private static final int SECURITY_PRODUCT_ID = 0;            // Always product 0

    public GetKeyRequest(NetworkNode networkNode, ConnectivityMonitor cm,
                         LanTransportContext ltc, ResponseHandler handler) {
        // GET https://{ip}/di/v1/products/0/security
        super(networkNode, cm, ltc, "security", 0, LanRequestType.GET, emptyMap(), handler);
    }

    @Override
    public Response execute() {
        // First, execute the normal LAN request
        Response response = super.execute();

        if (response.error == null) {
            // Parse the JSON response to extract the encryption key
            // Response format: {"key": "abcdef0123456789..."}
            SecurityPortProperties props = GsonProvider.get()
                .fromJson(response.data, SecurityPortProperties.class);

            String key = props.getKey();
            if (key != null && !key.isEmpty()) {
                return new Response(key, null); // Return just the key string
            }
            return new Response("Key missing in response", Error.REQUEST_FAILED);
        }
        return new Response(response.data, Error.REQUEST_FAILED);
    }
}
```

**Request:** `GET https://{ip}/di/v1/products/0/security`
**Response:** `{"key": "abcdef0123456789abcdef0123456789"}` (32-char hex string = 128-bit key)

---

## 9. AES Payload Encryption/Decryption

**File:** `connectivity/condor/lan/security/Crypto.java`

Some devices encrypt their HTTP response bodies. The decryption uses AES-128-CBC with PKCS7 padding and a zero IV.

```java
// --- Crypto.java (annotated) ---

public class Crypto {

    private static final String TRANSFORMATION = "AES/CBC/PKCS7Padding";
    private Listener listener;           // Called when decryption fails
    private final NetworkNode networkNode; // Source of encryption key

    // Listener interface for decryption failure notifications
    public interface Listener {
        void onDecryptionFailed(NetworkNode networkNode);
        // When this fires, LanCommunicationStrategy will trigger a key re-exchange
    }

    public Crypto(NetworkNode networkNode) {
        this.networkNode = networkNode;
    }

    // Create AES cipher with device's encryption key
    private Cipher createCipher(int mode, String encryptionKey) {
        Cipher cipher = Cipher.getInstance("AES/CBC/PKCS7Padding");

        // Convert hex string key to bytes
        // Key is a hex string like "abcdef..." -> BigInteger -> byte[]
        byte[] keyBytes = new BigInteger(encryptionKey, 16).toByteArray();

        // BigInteger may add a leading 0x00 byte for sign; strip it
        if (keyBytes[0] == 0) {
            keyBytes = Arrays.copyOfRange(keyBytes, 1, 17); // Take bytes [1..16]
        } else {
            keyBytes = Arrays.copyOf(keyBytes, 16); // Take first 16 bytes
        }

        // IV is all zeros (16 bytes)
        byte[] iv = new byte[]{0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0};

        cipher.init(mode, new SecretKeySpec(keyBytes, "AES"), new IvParameterSpec(iv));
        return cipher;
    }

    // AES decrypt raw bytes
    private byte[] aesDecryptData(byte[] ciphertext, String encryptionKey) {
        return createCipher(Cipher.DECRYPT_MODE, encryptionKey).doFinal(ciphertext);
    }

    // Main decryption entry point
    // Input: base64-encoded, AES-encrypted, random-byte-prepended JSON string
    // Output: plaintext JSON string, or null on failure
    public String decryptData(String data) {
        if (data.isEmpty()) return null;

        String encryptionKey = networkNode.getEncryptionKey();
        if (encryptionKey == null || encryptionKey.isEmpty()) {
            notifyDecryptionFailedListener();
            return null;
        }

        try {
            // Step 1: Strip leading/trailing whitespace (char-by-char loop, not String.trim())
            // Scans from both ends, skipping chars where compareTo(' ') <= 0

            // Step 2: Base64 decode
            byte[] decoded = ByteUtil.decodeFromBase64(trimmed);

            // Step 3: AES-CBC decrypt
            byte[] decrypted = aesDecryptData(decoded, encryptionKey);

            // Step 4: Remove 2 random prepended bytes
            // (The device prepends 2 random bytes before encryption as a nonce)
            byte[] jsonBytes = ByteUtil.removeRandomBytes(decrypted);

            // Step 5: Convert to string
            String json = new String(jsonBytes, Charset.defaultCharset());

            // Step 6: Validate it's valid JSON by parsing
            GsonProvider.get().fromJson(json, Map.class);

            return json;

        } catch (GeneralSecurityException e) {
            // Decryption failed - key may be wrong
            notifyDecryptionFailedListener(); // Triggers key re-exchange
            return null;
        } catch (IllegalArgumentException e) {
            // Base64 decode failed
            notifyDecryptionFailedListener();
            return null;
        }
    }

    // Notify listener (LanCommunicationStrategy) that decryption failed
    private void notifyDecryptionFailedListener() {
        if (listener != null) {
            listener.onDecryptionFailed(networkNode);
        }
    }
}
```

**Decryption pipeline:**
```
Encrypted response body (string)
    -> strip leading/trailing whitespace (char-by-char)
    -> base64 decode -> byte[]
    -> AES-128-CBC/PKCS7 decrypt (key=hex_to_bytes(encryption_key), IV=all_zeros)
    -> remove first 2 bytes (random nonce)
    -> UTF-8 decode -> JSON string
```

**Encryption parameters:**
- Algorithm: AES-128-CBC
- Padding: PKCS7
- Key: 128-bit from hex string (fetched from `/di/v1/products/0/security`)
- IV: 16 zero bytes
- Random prefix: 2 bytes prepended before encryption (stripped after decryption)

---

## 10. SSL/TLS and Certificate Pinning

**File:** `connectivity/condor/lan/security/SSLContextFactory.java`
**File:** `connectivity/condor/lan/security/SslPinTrustManager.java`
**File:** `connectivity/condor/lan/security/PublicKeyPin.java`

```java
// --- SSLContextFactory.java ---

public final class SSLContextFactory {

    // Creates a TLS context that trusts ALL certificates (no pinning)
    // Used for initial discovery/probe when no pin is stored yet
    public static SSLContext createTLSTrustEverythingSSLContext() {
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(null, new TrustManager[]{new X509TrustManager() {
            public void checkClientTrusted(X509Certificate[] c, String a) {} // Accept all
            public void checkServerTrusted(X509Certificate[] c, String a) {} // Accept all
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
        }}, new SecureRandom());
        return ctx;
    }

    // Creates a TLSv1.2 context with certificate public key pinning
    // Used for all ongoing communication after first connection
    public static SSLContext createTLSv12CertificatePinningSSLContext(NetworkNode networkNode) {
        SSLContext ctx = SSLContext.getInstance("TLSv1.2");
        ctx.init(null, new X509TrustManager[]{
            new SslPinTrustManager(networkNode) // Custom trust manager with pinning
        }, new SecureRandom());
        return ctx;
    }
}

// --- SslPinTrustManager.java ---
// Implements certificate public key pinning using SHA-256 hash

public class SslPinTrustManager implements X509TrustManager {

    private final NetworkNode networkNode;

    @Override
    public void checkServerTrusted(X509Certificate[] chain, String authType)
            throws CertificateException {
        // Validate inputs
        if (chain == null) throw new IllegalArgumentException("Certificate chain is null.");
        if (chain.length == 0) throw new IllegalArgumentException("Certificate chain is empty.");
        if (authType == null || authType.isEmpty())
            throw new IllegalArgumentException("Invalid key exchange algorithm.");

        // Compute pin from the server's leaf certificate
        PublicKeyPin serverPin = new PublicKeyPin(chain[0]);

        if (networkNode.getPin() == null) {
            // TOFU (Trust On First Use): store the pin
            networkNode.setPin(serverPin.toString());
            // This pin will be persisted to SQLite
        } else {
            // Compare against stored pin
            PublicKeyPin storedPin = new PublicKeyPin(networkNode.getPin());

            if (serverPin.equals(storedPin)) {
                // Pin matches - connection accepted
                return;
            }

            // PIN MISMATCH - store the new pin for user review
            networkNode.setMismatchedPin(serverPin.toString());
            throw new PinMismatchException(
                "The appliance's certificate doesn't match the stored pin.");
        }
    }
}

// --- PublicKeyPin.java ---
// SHA-256 hash of the certificate's public key, base64-encoded

public class PublicKeyPin {
    private final byte[] pinBytes; // 32 bytes (SHA-256)

    // From certificate: SHA-256(publicKey.getEncoded())
    public PublicKeyPin(Certificate certificate) {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        this.pinBytes = md.digest(certificate.getPublicKey().getEncoded());
    }

    // From stored base64 string
    public PublicKeyPin(String base64) {
        this.pinBytes = Base64.decode(base64, 0);
        if (pinBytes.length != 32) throw new IllegalArgumentException("Invalid pin");
    }

    public String toString() {
        return Base64.encodeToString(pinBytes, 0).trim();
    }
}
```

---

## 11. LAN Transport Context

**File:** `connectivity/condor/lan/context/LanTransportContext.java`

The factory that creates everything LAN-related.

```java
// --- LanTransportContext.java (key methods annotated) ---

public class LanTransportContext implements TransportContext {

    public static final long DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS = 30;
    private static final long NETWORK_REQUEST_TIMEOUT_SECONDS = 3;

    // Cached OkHttp clients per device (by cppId)
    private static final Map<String, OkHttpClient> okHttpClientMap = new LinkedHashMap();

    // Components
    private final Authentication authentication;
    private final ConnectivityMonitor connectivityMonitor;
    private final DeviceCache deviceCache;
    private final IPProvider ipProvider;
    private final SsidProvider ssidProvider;
    private final LanDiscoveryStrategy lanDiscoveryStrategy;
    private final MDNSDiscoveryStrategy mDNSDiscoveryStrategy;
    private final SSDPDiscoveryStrategy sSDPDiscoveryStrategy;
    private final MulticastLockControlPoint multicastLockControlPoint;

    public LanTransportContext(RuntimeConfiguration config) {
        this.multicastLockControlPoint = new MulticastLockControlPoint();
        this.connectivityMonitor = ConnectivityMonitor.forNetworkTransportLAN(config.getContext());

        // Clear HTTP client cache on WiFi availability change
        connectivityMonitor.addAvailabilityListener(it -> okHttpClientMap.clear());

        this.authentication = new Authentication(connectivityMonitor, this);
        this.deviceCache = new DeviceCache();
        this.ipProvider = new IPProvider(config.getContext());
        this.ssidProvider = new SsidProvider(config.getContext());
        this.lanDiscoveryStrategy = new LanDiscoveryStrategy(deviceCache, connectivityMonitor, ssidProvider, ipProvider, multicastLockControlPoint);
        this.mDNSDiscoveryStrategy = new MDNSDiscoveryStrategy(deviceCache, connectivityMonitor, ssidProvider, ipProvider, multicastLockControlPoint);
        this.sSDPDiscoveryStrategy = new SSDPDiscoveryStrategy(deviceCache, connectivityMonitor, ssidProvider, ipProvider, multicastLockControlPoint);
    }

    // Create a CommunicationStrategy for a specific device
    @Override
    public CommunicationStrategy createCommunicationStrategyFor(NetworkNode networkNode) {
        return new LanCommunicationStrategy(networkNode, connectivityMonitor, ssidProvider, this);
    }

    // Create or retrieve cached OkHttp client for a device
    // Uses TLSv1.2 with certificate pinning and LAN-only network binding
    public OkHttpClient createOrGetOkHttpClient(NetworkNode networkNode) {
        return createOrGetOkHttpClient(networkNode, okHttpClientMap, 30 /*seconds*/);
    }

    private OkHttpClient createOkHttpClient(NetworkNode networkNode, long timeout, boolean callTimeout) {
        OkHttpClient.Builder builder = new OkHttpClient.Builder();
        builder.connectTimeout(timeout, TimeUnit.SECONDS);
        builder.readTimeout(timeout, TimeUnit.SECONDS);
        if (callTimeout) {
            builder.writeTimeout(timeout, TimeUnit.SECONDS);
            builder.callTimeout(timeout, TimeUnit.SECONDS);
        }

        // Bind to LAN-only network (WiFi/Ethernet, no cellular)
        Network lanNetwork = createLANOnlyNetwork(); // Waits max 3s
        if (lanNetwork == null) return null;
        builder.socketFactory(lanNetwork.getSocketFactory());

        // Set up TLSv1.2 with certificate pinning
        SSLContext sslContext = SSLContextFactory.createTLSv12CertificatePinningSSLContext(networkNode);
        builder.sslSocketFactory(sslContext.getSocketFactory(), new SslPinTrustManager(networkNode));

        // Accept all hostnames (devices use IP addresses, not hostnames)
        builder.hostnameVerifier((hostname, session) -> true);

        // Restrict to TLS 1.2 with specific cipher suites
        ConnectionSpec spec = new ConnectionSpec.Builder(ConnectionSpec.MODERN_TLS)
            .tlsVersions(TlsVersion.TLS_1_2)
            .cipherSuites(
                CipherSuite.TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,     // ix0.i.f88908a1
                CipherSuite.TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,   // ix0.i.f88920e1
                CipherSuite.TLS_RSA_WITH_AES_128_CBC_SHA256             // ix0.i.f88940l0
            )
            .build();
        builder.connectionSpecs(Collections.singletonList(spec));

        return builder.build();
    }

    // Request a LAN-only Android Network (WiFi/Ethernet, no cellular)
    private Network createLANOnlyNetwork() {
        AtomicReference<Network> ref = new AtomicReference<>(null);
        CountDownLatch latch = new CountDownLatch(1);

        NetworkRequest request = new NetworkRequest.Builder()
            .addTransportType(3)  // TRANSPORT_ETHERNET
            .addTransportType(1)  // TRANSPORT_WIFI
            .removeTransportType(2) // Remove TRANSPORT_CELLULAR
            .removeTransportType(0) // Remove TRANSPORT_BLUETOOTH
            .removeTransportType(4) // Remove TRANSPORT_VPN
            .addCapability(11)    // NET_CAPABILITY_NOT_METERED (no longer required in newer Android)
            .addCapability(13)    // NET_CAPABILITY_INTERNET
            .addCapability(15)    // NET_CAPABILITY_NOT_VPN
            .addCapability(14)    // NET_CAPABILITY_NOT_RESTRICTED
            .removeCapability(17) // Remove NET_CAPABILITY_VALIDATED (allows devices without internet)
            .build();

        ConnectivityManager cm = (ConnectivityManager) context.getSystemService("connectivity");
        cm.requestNetwork(request, new ConnectivityManager.NetworkCallback() {
            public void onAvailable(Network network) {
                ref.set(network);
                latch.countDown();
            }
            public void onUnavailable() {
                latch.countDown();
            }
        });

        latch.await(3, TimeUnit.SECONDS); // Wait max 3 seconds
        cm.unregisterNetworkCallback(callback);
        return ref.get();
    }

    // Pairing entry point
    public void authenticate(Appliance appliance, Map<String, Object> params,
                           AuthenticationCallback callback) {
        NetworkNode node = appliance.getNetworkNode();
        authentication.authenticate(node, params, callback);
    }
}
```

---

## 12. Device Discovery (SSDP + mDNS)

**File:** `connectivity/condor/lan/discovery/BaseLanDiscoveryStrategy.java`

The app discovers devices using both SSDP and mDNS simultaneously.

```java
// --- BaseLanDiscoveryStrategy.java (annotated) ---

public abstract class BaseLanDiscoveryStrategy extends ObservableDiscoveryStrategy {

    public static final int ERROR_TRANSPORT_UNAVAILABLE = 111;
    public static final int ERROR_CONNECTION_UNAVAILABLE = 112;

    private final DeviceCache deviceCache;       // Caches discovered devices with TTL
    private final IPProvider ipProvider;          // Provides local IP
    private final SsidProvider ssidProvider;      // Provides current WiFi SSID
    protected final MulticastLockControlPoint multicastLockControlPoint; // Android multicast lock
    protected boolean isConnected;               // WiFi connected?
    protected boolean isStartRequested;           // Was start() called?

    // SSDP and mDNS control points (obfuscated class names)
    protected final SSDPControlPoint ssdpControlPoint;  // jw.c
    protected final MDNSControlPoint mMDNSControlPoint;  // hw.e

    private Set<String> modelIds; // Filter: only discover these model IDs (empty = all)

    // Device listener: bridges discovery events to the strategy
    private final DiscoveredDeviceListener deviceListener = new DiscoveredDeviceListener() {
        public void onDeviceAvailable(DiscoveredLanDevice device) {
            onDeviceDiscovered(device);
        }
        public void onDeviceUnavailable(DiscoveredLanDevice device) {
            onDeviceLost(device);
        }
    };

    // Cache expiration: when a device's cache entry expires, fire "lost" event
    private final DeviceCache.ExpirationCallback expirationCallback = networkNode -> {
        deviceCache.remove(networkNode.getCppId());
        notifyNetworkNodeLost(networkNode);
    };

    public BaseLanDiscoveryStrategy(DeviceCache cache, ConnectivityMonitor cm,
            SsidProvider ssid, IPProvider ip, MulticastLockControlPoint mcast) {
        this.deviceCache = cache;
        this.ssidProvider = ssid;
        this.ipProvider = ip;

        // Create SSDP and mDNS control points (subclasses can override to return null)
        this.ssdpControlPoint = createSSDPControlPoint();
        this.mMDNSControlPoint = createMDNSControlPoint();

        this.modelIds = Collections.EMPTY_SET;
        this.multicastLockControlPoint = mcast;

        // React to WiFi availability changes
        cm.addAvailabilityListener(availability -> {
            isConnected = availability.isAvailable();
            handleDiscoveryStateChanged();
        });
        isConnected = cm.isAvailable();
    }

    // Create NetworkNode from discovered device info
    public NetworkNode createNetworkNode(DiscoveredLanDevice device) {
        NetworkNode node = new NetworkNode();
        node.setBootId(Long.parseLong(device.getBootId())); // Can be -1 on parse error
        node.setCppId(device.getCppId());
        node.setIpAddress(device.getIpAddress());
        node.setName(device.getFriendlyName());
        node.setModelId(device.getModelNumber());
        node.setDeviceType(device.getModelName());
        node.setNetworkSsid(ssidProvider.getCurrentSsid());
        node.setExpirationPeriod(device.getExpirationPeriod());

        if (!node.isValid()) return null; // Reject invalid nodes
        return node;
    }

    // Called when a device is discovered via SSDP or mDNS
    public void onDeviceDiscovered(DiscoveredLanDevice device) {
        NetworkNode node = createNetworkNode(device);
        if (node == null || !nodePassesFilter(node)) return;

        CacheData existing = deviceCache.getCacheData(node.getCppId());
        if (existing == null) {
            // New device: add to cache with TTL
            deviceCache.add(node, expirationCallback, node.getExpirationPeriod());
        } else {
            // Known device: reset cache expiration timer
            existing.resetTimer();
        }
        notifyNetworkNodeDiscovered(node);
    }

    // Start/stop discovery
    public void start() { start(Collections.EMPTY_SET); }
    public void start(Set<String> modelIds) {
        this.modelIds = modelIds;
        this.isStartRequested = true;
        handleDiscoveryStateChanged(); // Will start SSDP + mDNS if connected
    }
    public void stop() {
        this.isStartRequested = false;
        handleDiscoveryStateChanged(); // Will stop SSDP + mDNS
    }

    // State machine: start/stop SSDP and mDNS based on connection + request state
    public void handleDiscoveryStateChanged() {
        if (isConnected && isStartRequested) {
            // Acquire Android multicast lock (required for mDNS/SSDP)
            if (multicastLockControlPoint.acquireMulticastLock()) {
                ssdpControlPoint.start();  // Start SSDP discovery
                mMDNSControlPoint.start(); // Start mDNS discovery
            }
        } else {
            // Stop both
            ssdpControlPoint.stop();
            mMDNSControlPoint.stop();
            multicastLockControlPoint.releaseMulticastLock();
        }
    }
}
```

**Discovery subclasses:**
- `LanDiscoveryStrategy` - uses both SSDP and mDNS (default)
- `MDNSDiscoveryStrategy` - mDNS only (overrides `createSSDPControlPoint()` to return null)
- `SSDPDiscoveryStrategy` - SSDP only (overrides `createMDNSControlPoint()` to return null)

---

## 13. UDP Event Subscriptions

**File:** `connectivity/condor/lan/subscription/LocalSubscriptionHandler.java`

Devices push state changes via UDP to the app.

```java
// --- LocalSubscriptionHandler.java (annotated) ---

public class LocalSubscriptionHandler extends SubscriptionHandler implements UdpEventListener {

    private int boundSubscriptionUdpPort = -1; // UDP port we're listening on
    private final Crypto crypto;               // Decrypts incoming UDP data
    private NetworkNode networkNode;
    private Set<SubscriptionEventListener> subscriptionEventListeners;
    private final UdpEventReceiver udpEventReceiver; // Singleton UDP socket manager

    // Called when a UDP packet is received
    @Override
    public synchronized void onUDPEventReceived(String data, String portName, String senderIP) {
        // Ignore if no listeners or no data
        if (subscriptionEventListeners == null || subscriptionEventListeners.isEmpty()) return;
        if (data == null || data.isEmpty()) return;
        if (senderIP == null || senderIP.isEmpty()) return;

        // Only process events from our device's IP
        if (networkNode.getIpAddress() != null && networkNode.getIpAddress().equals(senderIP)) {
            // Decrypt the data (same AES-128-CBC as HTTP responses)
            String decrypted = crypto.decryptData(data);

            if (decrypted == null) {
                // Decryption failed - notify listeners
                postSubscriptionEventDecryptionFailureOnUiThread(portName, subscriptionEventListeners);
            } else {
                // Success - post decrypted data to listeners on UI thread
                postSubscriptionEventOnUiThread(portName,
                    decrypted.getBytes(StandardCharsets.UTF_8), subscriptionEventListeners);
            }
        }
    }

    // Start receiving UDP events
    public void enableSubscription(NetworkNode node, Set<SubscriptionEventListener> listeners) {
        this.networkNode = node;
        this.subscriptionEventListeners = listeners;
        // Start UDP receiver, returns the bound port number
        this.boundSubscriptionUdpPort = udpEventReceiver.startReceivingEvents(this);
    }

    // Stop receiving UDP events
    public synchronized void disableSubscription() {
        this.subscriptionEventListeners = null;
        udpEventReceiver.stopReceivingEvents(this);
    }
}
```

The subscription flow:
1. App sends `POST /di/v1/products/{id}/` with `{"subscriber": "<appId>", "ttl": 300, "changeudp": <port>}`
2. Device ACKs with HTTP 200 and `X-Condor-Features: changeindication-port` header
3. Device sends encrypted UDP packets to the app's port when state changes
4. App decrypts with same AES-128-CBC key used for HTTP responses

---

## 14. Combined Communication Strategy

**File:** `connectivity/condor/core/communication/CombinedCommunicationStrategy.java`

Wraps both LAN and HSDP strategies. Picks the first available one (LAN preferred).

```java
// Key behavior:
// - Constructed with: CombinedCommunicationStrategy(lanStrategy, hsdpStrategy)
// - LinkedHashSet preserves insertion order: LAN is tried first
// - On availability change, waits 1000ms cool-down before switching
// - When switching transports: unsubscribes from old, resubscribes on new
// - If no strategy available, uses NullCommunicationStrategy (all ops return error)

public boolean isAvailable() {
    return firstAvailableStrategy() != null;
    // Iterates strategies in order, returns first where isAvailable() == true
}

// All operations delegate to findStrategy() which returns first available:
public void getProperties(String port, int productId, ResponseHandler handler) {
    findStrategy().getProperties(port, productId, handler);
}
```

---

## 15. HSDP Cloud Communication

**File:** `connectivity/condor/hsdp/HSDPCommunicationStrategy.java`
**File:** `connectivity/condor/hsdp/HSDPController.java`
**File:** `connectivity/condor/hsdp/HSDPMessenger.java`

HSDP cloud relay uses MQTT to communicate with devices via Philips cloud servers.

```java
// --- HSDPCommunicationStrategy.java ---
// Implements CommunicationStrategy using HSDP cloud MQTT

public class HSDPCommunicationStrategy extends ObservableCommunicationStrategy {

    private final NetworkNode networkNode;
    private final ConnectivityMonitor connectivityMonitor;
    private final HSDPMessenger hsdpMessenger;   // MQTT connection manager
    private final RequestQueue requestQueue;

    // Available if: internet connected AND device has an hsdpId
    // Note: does NOT require clientId/clientSecret (those are for LAN only)
    @Override
    public boolean isAvailable() {
        return connectivityMonitor.isAvailable() && networkNode.getHsdpId() != null;
    }

    // All operations create HSDPRemoteRequest objects and queue them
    @Override
    public void getProperties(String portName, int productId, ResponseHandler handler) {
        requestQueue.addRequest(new HSDPRemoteRequest(
            CondorOperation.GET_PROPS,   // "GetProps"
            networkNode.getHsdpId(),     // Target device's HSDP ID
            productId,
            portName,
            null,                        // No data for GET
            handler,
            hsdpMessenger));
    }

    @Override
    public void putProperties(Map<String, Object> data, String portName, int productId, ResponseHandler handler) {
        requestQueue.addRequest(new HSDPRemoteRequest(
            CondorOperation.PUT_PROPS,   // "PutProps"
            networkNode.getHsdpId(),
            productId,
            portName,
            data,
            handler,
            hsdpMessenger));
    }
}

// --- HSDPMessenger.java (interface) ---
public interface HSDPMessenger {
    void connect(Completable completable);
    void disconnect();
    HSDPConnectionState getConnectionState();
    void sendCommand(String hsdpId, ControlModel.Command command, Completable completable);
    void registerMessageListener(HSDPMessageListener listener);
    void unregisterMessageListener(HSDPMessageListener listener);
}

// --- HSDPController.java ---
// Implements HSDPMessenger using HSDP ControlServiceV1 (MQTT)

public class HSDPController implements HSDPMessenger {

    private final HSDPAuthentication authentication;
    private final HSDPConfiguration configuration;
    private final ServiceFactory serviceFactory;
    private ControlServiceV1 controlServiceV1; // HSDP MQTT service

    // Listens for MQTT events
    private final ControlServiceV1.Listener controlServiceListener = new ControlServiceV1.Listener() {
        public void onCommandReceived(ControlModel.Received received) {
            notifyMessageListeners(received); // Forward to HSDPRemoteRequest listeners
        }
        public void onConnected() {
            notifyConnectionListener(HSDPConnectionState.CONNECTED);
        }
        public void onDisconnected() {
            notifyConnectionListener(HSDPConnectionState.DISCONNECTED);
        }
    };

    // When tokens are refreshed, disconnect and reconnect
    private final HSDPAuthentication.AuthenticationListener authListener = new AuthenticationListener() {
        public void onAccessTokensRefreshed() {
            disconnect();
            connect(completable -> { /* log success/failure */ });
        }
        public void onAccessTokensRefreshError(HSDPAuthenticationError error) {
            // Notify connection error
        }
    };
}
```

---

## 16. HSDP Remote Request Protocol

**File:** `connectivity/condor/hsdp/messages/HSDPRemoteRequest.java`
**File:** `connectivity/condor/hsdp/messages/CondorControlMessage.java`
**File:** `connectivity/condor/hsdp/messages/CondorOperation.java`

The Condor protocol over MQTT uses JSON commands with a specific schema.

```java
// --- CondorOperation.java ---
// All supported operations
public enum CondorOperation {
    ADD_PROPS("AddProps"),
    CHANGE_INDICATION("ChangeIndication"),  // Subscription push event
    DEL_PROPS("DelProps"),
    EXEC_METHOD("ExecMethod"),
    GET_PORTS("GetPorts"),
    GET_PRODS("GetProds"),
    GET_PROPS("GetProps"),
    PUT_PROPS("PutProps"),
    SUBSCRIBE("Subscribe"),
    UNKNOWN("Unknown"),
    UNSUBSCRIBE("Unsubscribe");
}

// --- CondorControlMessage.java ---
// JSON message structure
public class CondorControlMessage {
    @SerializedName("condorVersion") public String condorVersion; // Always "1"
    @SerializedName("op")            public String operation;     // CondorOperation string
    @SerializedName("path")          public String path;         // "{productId}/{portName}"
    @SerializedName("status")        public Integer status;      // Response status code
    @SerializedName("values")        public Object values;       // Request/response data
}

// --- HSDPRemoteRequest.java ---
// Synchronous request-response over MQTT

public class HSDPRemoteRequest extends Request implements HSDPMessageListener {

    private static final String CONDOR_VERSION = "1";
    private static final int HSDP_DEVICE_CONTROL_TIMEOUT_MS = 30000; // 30s timeout
    private static final int TIME_TO_LIVE_S = 30;

    private String identifier; // UUID for matching request to response
    private CountDownLatch sendCommandLatch;  // Waits for MQTT publish ACK
    private CountDownLatch responseLatch;     // Waits for device response
    private String responseString;
    private boolean commandRejected;

    // Build the command JSON
    private Map<String, Object> createCommandDetail() {
        HashMap detail = new HashMap();
        detail.put("condorVersion", "1");                     // Always version 1
        detail.put("op", condorOperation.toString());         // e.g., "GetProps"
        detail.put("path", productId + "/" + portName);       // e.g., "1/air"
        detail.put("values", ByteUtil.encodeByteArraysToBase64(values));
        if (subscriptionTtl != null) {
            detail.put("ttl", subscriptionTtl);
        }
        return detail;
    }

    @Override
    public Response execute() {
        // Step 1: Create latches for synchronous wait
        sendCommandLatch = new CountDownLatch(1);
        responseLatch = new CountDownLatch(1);

        // Step 2: Register as MQTT message listener
        messenger.registerMessageListener(this);

        // Step 3: Send MQTT command
        // Command includes: identifier (UUID), detail (condor message), requireAck=true
        messenger.sendCommand(hsdpId, new ControlModel.Command(
            identifier, createCommandDetail(),
            null, null, null, null, null, null, null,
            true,   // requireAck
            null, 30 // TTL 30 seconds
        ), sendCommandCompletion);

        // Step 4: Wait for MQTT publish acknowledgment (max 30s)
        if (!sendCommandLatch.await(30000, TimeUnit.MILLISECONDS) || sendCommandError != null) {
            messenger.unregisterMessageListener(this);
            return new Response(null, Error.SEND_FAILED);
        }

        // Step 5: Wait for device response (max 30s)
        if (!responseLatch.await(30000, TimeUnit.MILLISECONDS)) {
            // Timeout
        }

        messenger.unregisterMessageListener(this);

        // Step 6: Process response
        if (commandRejected) {
            return new Response(null, Error.REJECTED);
        }
        if (responseString == null) {
            return new Response(null, Error.REQUEST_FAILED);
        }

        // Extract status and values from Condor message
        Integer status = extractStatus(responseString);
        if (status != null && status != 0) {
            return new Response(null, Error.getErrorForCode(status));
        }
        String data = extractData(responseString); // JSON-serialize the "values" field
        return new Response(data, null);
    }

    // MQTT message callback
    @Override
    public void messageReceived(ControlModel.Received received) {
        String type = received.getType();

        if ("accepted".equals(type)) {
            // Command was accepted by HSDP (not device response yet)
            // Ignore - we're waiting for "notification"
        } else if ("rejected".equals(type)) {
            commandRejected = true;
            responseLatch.countDown();
        } else if ("notification".equals(type)) {
            // This is the actual device response
            if (received.getCommand() != null
                && received.getCommand().getCmdName().equals(identifier)) {
                // Match our request UUID
                responseString = received.getCommand().getStatusDetailAsJsonString();
                responseLatch.countDown();
            }
        }
    }
}
```

---

## 17. HSDP Transport Context

**File:** `connectivity/condor/hsdp/HSDPTransportContext.java`

```java
// Creates the HSDP communication stack
public class HSDPTransportContext implements TransportContext {

    public HSDPTransportContext(RuntimeConfiguration config, HSDPConfiguration hsdpConfig) {
        // Internet-capable connectivity monitor (not LAN-only)
        this.connectivityMonitor = ConnectivityMonitor.forNetworkCapabilityInternet(config.getContext());
        this.hsdpConfiguration = hsdpConfig;

        // Build the MQTT stack:
        // ServiceFactory -> HSDPController -> HSDPCommandQueue -> HSDPMessenger
        ServiceFactory serviceFactory = new ServiceFactory();
        serviceFactory.getConfiguration().setLoggingEnabled(true);

        this.hsdpMessenger = new HSDPCommandQueue(
            new HSDPController(
                hsdpConfig,
                serviceFactory,
                new HSDPAuthentication(serviceFactory, hsdpConfig)
            )
        );
    }

    @Override
    public CommunicationStrategy createCommunicationStrategyFor(NetworkNode networkNode) {
        return new HSDPCommunicationStrategy(networkNode, connectivityMonitor, hsdpMessenger);
    }

    // Connection states
    public enum HSDPConnectionState {
        DISCONNECTED,
        CONNECTED,
        UNKNOWN
    }
}
```

---

## 18. DaConnect Authentication Service

**File:** `cl/daconnect/authentication/DaAuthenticationService.java`

The cloud authentication layer wraps credential providers and manages the transition between OIDC (Gigya) and HSDP (Philips HealthSuite) identities. For FUSION devices, this service orchestrates a two-stage token exchange:

1. **OIDC Authentication:** The user logs in via Gigya, receiving OIDC `id_token` and `access_token`.
2. **SAS Token Exchange:** The OIDC tokens are exchanged for HSDP tokens via the **SAS API** (`https://sas.eu-da.iot.versuni.com/api/user/self/hsdp-token`).
   - **Request:** POST with `Authorization: Bearer {OIDC_access_token}` and JSON body `{"idToken": "{OIDC_id_token}", "exchangeFor": "HSDP"}`.
   - **Headers:** Must include `Accept: application/vnd.oneka.v2.0+json` and `Content-Type: application/vnd.oneka.v2.0+json`.
   - **Response:** `SasHsdpTokensResponse` containing `accessToken`, `refreshToken`, and **`signedToken`**.
3. **HSDP Identity:** The resulting `accessToken` is a Philips-specific HSDP IAM token, and the `signedToken` is a required cryptographic proof for AWS IoT Custom Authorizers.

---

## 19. MQTT Connection Info

**File:** `cl/daconnect/authentication/models/MqttConnectionInfo.java`
**File:** `decompiled/smali_classes7/com/philips/cl/daconnect/device_control/mqtt/DaMqttClientImpl.smali`

MQTT authentication for FUSION devices uses AWS IoT Core with a **Custom Authorizer**.

```java
// Data class holding MQTT connection details
public final class MqttConnectionInfo {
    private final String accessToken;       // HSDP access token from SAS exchange
    private final String mqttSignature;     // Signature from /user/self/signature
    private final String tenant;            // e.g., "da"
    private final WebSocketUrl webSocketUrl; // wss:// endpoint URL
}
```

**MQTT WebSocket Headers:**
When connecting to the WSS endpoint, the following headers are required:
- `x-amz-customauthorizer-name`: Always `"CustomAuthorizer"`
- `x-amz-customauthorizer-signature`: The `mqttSignature` obtained from the signature endpoint.
- `token-header`: `Bearer {accessToken}` (The HSDP access token).

**The Role of `signedToken`:**
While the `token-header` uses the `accessToken`, the `signedToken` (obtained from the SAS exchange) is often required as the *underlying token* passed to the `ControlServiceV1.connect(accessToken, signedToken)` method. For some firmware versions of FUSION devices, failing to provide the `signedToken` or using a token without the correct `aud` claim (which the SAS exchange provides) results in connection termination.

---

---

## 20. Credential Storage (SQLite)

**File:** `connectivity/condor/core/store/NetworkNodeDatabase.java`

All device credentials are persisted in a SQLite database called `network_node`.

```java
// --- NetworkNodeDatabase.java (annotated) ---

public class NetworkNodeDatabase {

    // Save a NetworkNode to SQLite
    public long save(NetworkNode networkNode) {
        ContentValues values = new ContentValues();
        values.put("cppid", networkNode.getCppId());
        values.put("mac_address", networkNode.getMacAddress());
        values.put("bootid", networkNode.getBootId());
        values.put("encryption_key", networkNode.getEncryptionKey()); // AES key stored in plain text!
        values.put("dev_name", networkNode.getName());
        values.put("lastknown_network", networkNode.getNetworkSsid());
        values.put("is_paired", networkNode.getPairedState().ordinal());
        values.put("last_paired", networkNode.getLastPairedTime());
        values.put("ip_address", networkNode.getIpAddress());
        values.put("device_type", networkNode.getDeviceType());
        values.put("model_id", networkNode.getModelId());
        values.put("https", true);             // Always HTTPS
        values.put("pin", networkNode.getPin()); // TLS certificate pin
        values.put("client_secret", networkNode.getClientSecret()); // Pairing secret in plain text!
        values.put("client_id", networkNode.getClientId());         // Our auth client ID
        values.put("hsdpid", networkNode.getHsdpId());              // Cloud device ID
        return databaseHelper.insertRow(values);
    }

    // Load all NetworkNodes from SQLite
    public List<NetworkNode> getAll() {
        Cursor cursor = databaseHelper.query(null, null);
        // Reads all columns: cppid, mac_address, bootid, encryption_key,
        // dev_name, lastknown_network, is_paired, last_paired, ip_address,
        // device_type, model_id, pin, client_id, client_secret, hsdpid
        // Constructs NetworkNode objects from each row
    }
}
```

---

## 21. Database Schema Evolution

**File:** `connectivity/condor/core/store/NetworkNodeDatabaseSchema.java`

```sql
-- Current schema (version 9)
CREATE TABLE IF NOT EXISTS network_node(
    _id INTEGER NOT NULL UNIQUE,
    cppid TEXT UNIQUE,            -- Unique device identifier
    mac_address TEXT,             -- Added in v7
    bootid NUMERIC,              -- Device boot counter
    encryption_key TEXT,          -- AES-128 key (hex string)
    dev_name TEXT,                -- Friendly name
    lastknown_network TEXT,       -- WiFi SSID
    is_paired SMALLINT NOT NULL DEFAULT 0,  -- PairingState ordinal
    last_paired NUMERIC,         -- Timestamp
    ip_address TEXT,             -- LAN IP
    device_type TEXT,            -- Model name (renamed from model_name in v5)
    model_id TEXT,               -- Model number (renamed from model_type in v3)
    https SMALLINT NOT NULL DEFAULT 0,  -- Added in v2 (always true now)
    pin TEXT,                    -- TLS cert pin (added in v4)
    mismatched_pin TEXT,         -- New pin when cert changes (added in v6)
    client_id TEXT,              -- Auth client ID (added in v8)
    client_secret TEXT,          -- Auth secret (added in v8)
    hsdpid TEXT,                 -- HSDP cloud device ID (added in v9)
    PRIMARY KEY(_id)
);
```

**Schema migration history:**
- v1: Initial (cppid, bootid, encryption_key, dev_name, lastknown_network, is_paired, last_paired, ip_address, model_name, model_type)
- v2: Added `https` column
- v3: Renamed `model_type` to `model_id`
- v4: Added `pin` column
- v5: Renamed `model_name` to `device_type`
- v6: Added `mismatched_pin` column
- v7: Added `mac_address` column (default = cppid)
- v8: Added `client_id` and `client_secret` columns
- v9: Added `hsdpid` column

---

## 22. Crypto Utilities (ByteUtil)

**File:** `connectivity/condor/core/security/ByteUtil.java`

```java
// --- ByteUtil.java (annotated) ---

public class ByteUtil {

    static final int RANDOM_BYTE_ARR_SIZE = 2;   // Random bytes prepended before AES encryption
    static final int RANDOM_KEY_LENGTH = 224;     // Bit length for generateRandomNumber()

    // Generate a random 128-bit key, base64-encoded
    // Used to create client_id during pairing
    public static String create128bitBase64EncodedKey() {
        return encodeToBase64(createRandomByteArray(16)); // 16 bytes = 128 bits
    }

    // Create array of random bytes
    public static byte[] createRandomByteArray(int length) {
        byte[] bytes = new byte[length];
        new SecureRandom().nextBytes(bytes);
        return bytes;
    }

    // SHA-256 hash
    public static byte[] createSHA256HashFrom(byte[] data) {
        return MessageDigest.getInstance("SHA-256").digest(data);
    }

    // Concatenate multiple byte arrays
    public static byte[] concatenate(byte[]... arrays) {
        int totalLen = 0;
        for (byte[] a : arrays) totalLen += a.length;
        byte[] result = new byte[totalLen];
        int offset = 0;
        for (byte[] a : arrays) {
            System.arraycopy(a, 0, result, offset, a.length);
            offset += a.length;
        }
        return result;
    }

    // Base64 encode (NO_WRAP flag = 2)
    public static String encodeToBase64(byte[] data) {
        return Base64.encodeToString(data, Base64.NO_WRAP);
    }

    // Base64 decode
    public static byte[] decodeFromBase64(String str) throws BadPaddingException {
        return Base64.decode(str.getBytes(Charset.defaultCharset()), Base64.DEFAULT);
    }

    // Prepend 2 random bytes to data (used before AES encryption)
    public static byte[] addRandomBytes(byte[] data) {
        byte[] random = createRandomByteArray(2);
        byte[] result = new byte[data.length + 2];
        System.arraycopy(random, 0, result, 0, 2);
        System.arraycopy(data, 0, result, 2, data.length);
        return result;
    }

    // Remove first 2 bytes (strip random prefix after AES decryption)
    public static byte[] removeRandomBytes(byte[] data) {
        if (data.length < 3) return data;
        return Arrays.copyOfRange(data, 2, data.length);
    }

    // Convert hex string to byte array
    public static byte[] hexToBytes(String hex) {
        byte[] bytes = new byte[hex.length() / 2];
        for (int i = 0; i < bytes.length; i++) {
            bytes[i] = Integer.valueOf(hex.substring(i * 2, i * 2 + 2), 16).byteValue();
        }
        return bytes;
    }

    // Convert byte array to uppercase hex string
    public static String bytesToCapitalizedHex(byte[] data) {
        char[] HEX = "0123456789ABCDEF".toCharArray();
        char[] result = new char[data.length * 2];
        for (int i = 0; i < data.length; i++) {
            result[i * 2] = HEX[(data[i] & 0xFF) >>> 4];
            result[i * 2 + 1] = HEX[data[i] & 0x0F];
        }
        return new String(result);
    }

    // Encode any byte[] values in a Map to base64 strings
    public static Map<String, Object> encodeByteArraysToBase64(Map<String, Object> map) {
        HashMap result = new HashMap(map);
        for (String key : result.keySet()) {
            if (result.get(key) instanceof byte[]) {
                result.put(key, encodeToBase64((byte[]) result.get(key)));
            }
        }
        return result;
    }
}
```

---

## 23. Request/Response Framework

**File:** `connectivity/condor/core/request/Request.java`
**File:** `connectivity/condor/core/request/Response.java`
**File:** `connectivity/condor/core/request/RequestQueue.java`
**File:** `connectivity/condor/core/request/Error.java`

```java
// Request is the base class for all requests (LAN and HSDP)
public abstract class Request {
    protected Map<String, Object> mDataMap;    // Request data
    protected ResponseHandler mResponseHandler; // Callback
    public abstract Response execute();         // Subclasses implement this
}

// Response wraps result data and error
public class Response {
    public final String data;  // JSON string on success, error message on failure
    public final Error error;  // null on success
}

// RequestQueue executes requests serially on a background thread
public class RequestQueue {
    // addRequest(Request) - adds to end of queue
    // addRequestInFrontOfQueue(Request) - priority insert (used for key exchange)
    // Each request's execute() is called, then ResponseHandler.onSuccess/onError
}

// Error enum with error codes
// NO_ERROR(0), REQUEST_FAILED(1), EMPTY_RESPONSE(2), NOT_UNDERSTOOD(3),
// SEND_FAILED(4), REQUEST_UNAUTHORIZED(5), REJECTED(6), BUSY(7),
// IOEXCEPTION(8), OUT_OF_MEMORY(9), INVALID_PARAMETER(10),
// NO_REQUEST_DATA(11), NO_TRANSPORT_AVAILABLE(12), INSECURE_CONNECTION(13),
// NOT_CONNECTED(14), NOT_SUBSCRIBED(15), CANNOT_CONNECT(16)
```

---

## Summary: Complete Protocol Reference

### LAN Device Communication

| Step | URL | Method | Auth | Body |
|------|-----|--------|------|------|
| Probe | `https://{ip}/di/v1/products/{0,1}/` | GET | None | - |
| Pair | `https://{ip}/auth/v1/` | PUT | None | `{"id": "<client_id>"}` |
| Pair (challenge) | `https://{ip}/auth/v1/` | PUT | None | `{"id": "<client_id>", "key": "<SHA256(seed+id)>"}` |
| Get key | `https://{ip}/di/v1/products/0/security` | GET | PhilipsCondor | - |
| Get state | `https://{ip}/di/v1/products/{id}/{port}` | GET | PhilipsCondor | - |
| Set state | `https://{ip}/di/v1/products/{id}/{port}` | PUT | PhilipsCondor | JSON data |
| Subscribe | `https://{ip}/di/v1/products/{id}/{port}` | POST | PhilipsCondor | `{"subscriber":"...","ttl":300,"changeudp":<port>}` |
| Unsubscribe | `https://{ip}/di/v1/products/{id}/{port}` | DELETE | PhilipsCondor | Unsubscription data |

### PhilipsCondor Auth Header

```
Authorization: PHILIPS-Condor base64(clientId_bytes + SHA256(challenge_bytes + clientId_bytes + clientSecret_bytes))
```

### AES-128-CBC Decryption

```
Parameters:
  - Algorithm: AES/CBC/PKCS7Padding
  - Key: encryption_key (hex string -> 16 bytes)
  - IV: 16 zero bytes
  - Pipeline: base64_decode -> AES_decrypt -> strip_first_2_bytes -> UTF-8
```

### HSDP Cloud Protocol (Condor over MQTT)

```json
{
    "condorVersion": "1",
    "op": "GetProps",
    "path": "1/air",
    "values": {...},
    "ttl": 30
}
```

Response types: `"accepted"` (ACK), `"rejected"` (error), `"notification"` (data).

### Key Storage (SQLite: network_node table)

| Column | Type | Purpose |
|--------|------|---------|
| _id | INTEGER NOT NULL UNIQUE | SQLite row ID (primary key) |
| cppid | TEXT UNIQUE | Device identifier |
| client_id | TEXT | Base64 128-bit random key (locally generated) |
| client_secret | TEXT | Device-issued secret (from pairing) |
| encryption_key | TEXT | AES-128 hex key (from `/security` endpoint) |
| hsdpid | TEXT | HSDP cloud device ID |
| pin | TEXT | SHA-256 of TLS public key (base64) |
| is_paired | SMALLINT | 0=PAIRED, 1=NOT_PAIRED, 2=UNPAIRED, 3=PAIRING |

---

## 24. Appliance Model

**File:** `connectivity/condor/core/appliance/Appliance.java`

Abstract base class representing a Philips device.

```java
public abstract class Appliance implements Availability<Appliance> {
    protected final CommunicationStrategy communicationStrategy; // LAN, HSDP, or Combined
    private final DevicePort devicePort;                         // Default "device" port
    protected final NetworkNode networkNode;                     // Device identity + credentials
    private final Set<CondorPort> ports = new HashSet();         // All registered ports

    public Appliance(NetworkNode networkNode, CommunicationStrategy... strategies) {
        this.networkNode = networkNode;
        // If multiple strategies provided, wrap in CombinedCommunicationStrategy
        if (strategies.length == 1) {
            this.communicationStrategy = strategies[0];
        } else {
            this.communicationStrategy = new CombinedCommunicationStrategy(strategies);
        }
        // Every appliance automatically gets a DevicePort
        DevicePort devicePort = new DevicePort(this.communicationStrategy);
        this.devicePort = devicePort;
        addPort(devicePort);
    }

    // Register a port (sets the NetworkNode reference on it)
    public void addPort(CondorPort port) {
        port.setNetworkNode(this.networkNode);
        this.ports.add(port);
    }

    // Find a port by class type
    public <P extends CondorPort> P getPort(Class<P> cls) {
        for (CondorPort port : getAllPorts()) {
            if (port.getClass().isAssignableFrom(cls)) return (P) port;
        }
        return null;
    }

    // Availability delegates to communication strategy
    public boolean isAvailable() { return communicationStrategy.isAvailable(); }

    // Equality is based on NetworkNode (which uses cppId)
    public boolean equals(Object obj) {
        if (obj instanceof Appliance) return networkNode.equals(((Appliance) obj).getNetworkNode());
        return false;
    }

    public abstract String getDeviceType(); // Subclasses define this
}
```

---

## 25. Appliance Manager

**File:** `connectivity/condor/core/appliance/ApplianceManager.java`

Manages the lifecycle of appliances: discovery, persistence, and event notification.

```java
public class ApplianceManager {
    private final ApplianceDatabase applianceDatabase;
    private final ApplianceFactory applianceFactory;
    private final NetworkNodeDatabase networkNodeDatabase;
    private final Set<ApplianceListener> applianceListeners = new CopyOnWriteArraySet();
    private final Map<String, Appliance> knownAppliances = new ConcurrentHashMap();      // All ever-seen
    private final Map<String, Appliance> discoveredAppliances = new ConcurrentHashMap(); // Currently visible

    public interface ApplianceListener {
        void onApplianceFound(Appliance appliance);
        void onApplianceLost(Appliance appliance);
        void onApplianceUpdated(Appliance appliance);
    }

    public ApplianceManager(Set<DiscoveryStrategy> strategies, ApplianceFactory factory,
                           NetworkNodeDatabase db, ApplianceDatabase appDb) {
        // Register as listener on all discovery strategies
        for (DiscoveryStrategy strategy : strategies) {
            strategy.addDiscoveryListener(discoveryListener);
        }
        this.applianceFactory = factory;
        // Load previously-paired appliances from SQLite
        loadAllAddedAppliancesFromDatabase();
    }

    // Core logic: when a NetworkNode is discovered or loaded from DB
    private Appliance processDiscoveredOrLoadedNetworkNode(NetworkNode node) {
        String cppId = node.getCppId();

        // Already discovered? Just update it
        if (discoveredAppliances.containsKey(cppId)) {
            updateAppliance(node);
            return discoveredAppliances.get(cppId);
        }

        // Known from DB but not yet discovered? Mark as discovered
        if (knownAppliances.containsKey(cppId)) {
            Appliance appliance = knownAppliances.get(cppId);
            discoveredAppliances.put(cppId, appliance);
            notifyApplianceFound(appliance);
            return appliance;
        }

        // Brand new: ask factory to create if it can
        if (applianceFactory.canCreateApplianceForNode(node)) {
            Appliance appliance = applianceFactory.createApplianceForNode(node);
            knownAppliances.put(cppId, appliance);
            discoveredAppliances.put(cppId, appliance);
            notifyApplianceFound(appliance);
            return appliance;
        }
        return null;
    }

    // Persist appliance to SQLite
    public boolean storeAppliance(Appliance appliance) {
        long id = networkNodeDatabase.save(appliance.getNetworkNode());
        applianceDatabase.save(appliance);
        return id != -1;
    }

    // Remove appliance from SQLite
    public boolean forgetStoredAppliance(Appliance appliance) {
        int deleted = networkNodeDatabase.delete(appliance.getNetworkNode());
        if (deleted > 0) applianceDatabase.delete(appliance);
        return deleted > 0;
    }

    // Load all from DB, create Appliance objects, register property change listeners
    private void loadAllAddedAppliancesFromDatabase() {
        for (NetworkNode node : networkNodeDatabase.getAll()) {
            Appliance appliance = processDiscoveredOrLoadedNetworkNode(node);
            if (appliance != null) {
                applianceDatabase.loadDataForAppliance(appliance);
                // Auto-save to DB when any property changes
                node.addPropertyChangeListener(event -> networkNodeDatabase.save(node));
            }
        }
    }
}
```

---

## 26. ApplianceFactory Interface

**File:** `connectivity/condor/core/appliance/ApplianceFactory.java`

```java
public interface ApplianceFactory {
    boolean canCreateApplianceForNode(NetworkNode networkNode);
    Appliance createApplianceForNode(NetworkNode networkNode);
}
```

---

## 27. Condor Entry Point (SDK Initialization)

**File:** `connectivity/condor/core/CondorEntryPoint.java`

The main entry point for the Condor SDK. Singleton pattern.

```java
public final class CondorEntryPoint {
    // Weak reference to enforce singleton
    public static WeakReference<CondorEntryPoint> instanceWeakReference = new WeakReference<>(null);
    private static final AppIdProvider APP_ID_PROVIDER = new AppIdProvider();

    private final ApplianceManager applianceManager;
    private final Set<DiscoveryStrategy> discoveryStrategies;
    private TransportContext[] transportContexts;

    public CondorEntryPoint(ApplianceFactory factory, RuntimeConfiguration config,
                           ApplianceDatabase appDb, TransportContext... transports) {
        if (instanceWeakReference.get() != null) {
            throw new UnsupportedOperationException("Only one instance allowed.");
        }
        instanceWeakReference = new WeakReference<>(this);

        this.transportContexts = transports;
        // Collect discovery strategies from each transport
        for (TransportContext tc : transports) {
            DiscoveryStrategy ds = tc.getDiscoveryStrategy();
            if (ds != null) discoveryStrategies.add(ds);
        }
        // Create the appliance manager with all strategies
        this.applianceManager = new ApplianceManager(
            discoveryStrategies, factory,
            new NetworkNodeDatabaseFetcher().getNetworkNodeDatabase(config), appDb);
    }

    public void startDiscovery() { startDiscovery(Collections.EMPTY_SET); }
    public void startDiscovery(Set<String> modelIds) {
        for (DiscoveryStrategy ds : discoveryStrategies) ds.start(modelIds);
    }
    public void stopDiscovery() {
        for (DiscoveryStrategy ds : discoveryStrategies) ds.stop();
    }

    // Get a specific transport context by class
    public <T extends TransportContext> T getTransportContext(Class<T> cls) throws TransportUnavailableException {
        for (TransportContext tc : transportContexts) {
            if (tc.getClass().equals(cls)) return cls.cast(tc);
        }
        throw new TransportUnavailableException("Requested transport context is not available");
    }
}
```

---

## 28. CondorPort (Port Abstraction Layer)

**File:** `connectivity/condor/core/port/CondorPort.java`

This is the central abstraction for device communication. Every device "port" (air, security, firmware, pairing, etc.) extends this class.

```java
public abstract class CondorPort<P extends CondorPortProperties> implements CondorPortApi<P> {

    protected CommunicationStrategy communicationStrategy;
    protected Gson gson;
    private P mCachedProperties;                    // Last-known state
    private boolean mIsApplyingChanges;             // True while putProperties is in flight
    private final Type propertiesType;              // Reflection-resolved type parameter P
    private AtomicBoolean isRequestInProgress;      // Serializes requests
    private boolean isSubscribed;                   // Subscription state

    // Queues for pending operations (serialized execution)
    private final List<Consumer<Result<P>>> getPropertiesCallbacks;
    private final List<Consumer<Result<P>>> subscribeCallbacks;
    private final List<Consumer<Result<P>>> unsubscribeCallbacks;
    private final Queue<ExecMethodInfo> execMethodInfoQueue;
    private final Queue<PutPropertiesInfo> putPropertiesQueue;

    // Subscription listeners + auto-resubscription
    private final Set<PortSubscriptionListener<P>> mPortSubscriptionListeners;
    private final Runnable resubscriptionRunnable;  // Periodic resubscription
    private final SubscriptionEventListener subscriptionEventListener; // UDP/MQTT event handler

    // Must be implemented by subclasses:
    public abstract String getCondorPortName();  // e.g., "air", "security", "pairing"
    public abstract int getCondorProductId();    // 0 or 1

    // Request serialization: only one request at a time
    private void tryToPerformNextRequest() {
        synchronized (this) {
            if (isRequestInProgress.get()) return;
            isRequestInProgress.set(true);
            // Priority order: putProperties > subscribe > unsubscribe > getProperties > execMethod
            if (isPutPropertiesRequested())      performPutProperties();
            else if (isSubscribeRequested())      performSubscribe();
            else if (isUnsubscribeRequested())    performUnsubscribe();
            else if (isGetPropertiesRequested())  performGetProperties();
            else if (isExecMethodRequested())     performExecMethod();
            else isRequestInProgress.set(false);
        }
    }

    // GET device state
    private void performGetProperties() {
        communicationStrategy.getProperties(getCondorPortName(), getCondorProductId(),
            new ResponseHandler() {
                void onSuccess(byte[] data) {
                    processResponse(data);                    // Parse + cache
                    flushGetPropertiesCallbacks(new Result.SuccessResult(getCachedProperties()));
                    requestCompleted();
                }
                void onError(Error error, String msg) {
                    flushGetPropertiesCallbacks(new Result.FailureResult(error, msg));
                    requestCompleted();
                }
            });
    }

    // PUT device state
    private void performPutProperties() {
        setIsApplyingChanges(true);
        PutPropertiesInfo info = putPropertiesQueue.remove();
        communicationStrategy.putProperties(info.properties, getCondorPortName(), getCondorProductId(),
            new ResponseHandler() {
                void onSuccess(byte[] data) {
                    if (!isPutPropertiesRequested()) setIsApplyingChanges(false);
                    processResponse(data);
                    info.callback.accept(new Result.SuccessResult(getCachedProperties()));
                    requestCompleted();
                }
                void onError(Error error, String msg) {
                    if (!isPutPropertiesRequested()) setIsApplyingChanges(false);
                    info.callback.accept(new Result.FailureResult(error, msg));
                    requestCompleted();
                }
            });
    }

    // SUBSCRIBE for push events
    private void performSubscribe() {
        communicationStrategy.subscribe(getCondorPortName(), getCondorProductId(),
            communicationStrategy.getSubscriptionTtl(), responseHandler);
        // On success: starts auto-resubscription timer (TTL seconds)
    }

    // Process response bytes -> parse JSON -> merge into cached properties
    public boolean processResponse(byte[] data) {
        if (data == null || data.length == 0) return false;
        String json = communicationStrategy.processByteArrayToJsonString(data);
        P properties = propertiesFromJsonString(json);
        if (properties == null) return false;
        setPortProperties(properties); // Update cache
        return true;
    }

    // Parse JSON and MERGE with cached properties (not replace)
    public P propertiesFromJsonString(String json) {
        JsonObject incoming = gson.fromJson(json, JsonObject.class);
        JsonObject existing = gson.toJsonTree(mCachedProperties, propertiesType);
        // Merge: add all incoming keys to existing
        for (String key : incoming.keySet()) {
            existing.add(key, incoming.get(key));
        }
        return gson.fromJson(existing, propertiesType);
    }

    // Subscription event handler: decrypts + processes incoming push data
    // subscriptionEventListener.onSubscriptionEventReceived(portName, data):
    //   -> processResponse(data)
    //   -> notify PortSubscriptionListeners
    //
    // subscriptionEventListener.onSubscriptionEventDecryptionFailed(portName):
    //   -> fall back to getProperties() (full reload)
}
```

**Key behavior:** Properties are MERGED, not replaced. When a subscription event arrives with partial data, it's merged into the cached state.

---

## 29. SecurityPortProperties

**File:** `connectivity/condor/core/port/common/SecurityPortProperties.java`

```java
public final class SecurityPortProperties implements CondorPortProperties {
    @SerializedName("key")     private final String key;     // Encryption key (hex string)
    @SerializedName("nextkey") private final String nextKey;  // Next key (for key rotation)
}
// Response from GET /di/v1/products/0/security:
// {"key": "abcdef0123456789...", "nextkey": null}
```

---

## 30. DeviceCloudPairingPort

**File:** `connectivity/condor/core/port/common/DeviceCloudPairingPort.java`

Handles cloud-based pairing via HSDP (remote method invocation).

```java
public class DeviceCloudPairingPort extends CondorPort<DeviceCloudPairingPortProperties> {
    static final String METHOD_PAIR = "Pair";
    static final String METHOD_UNPAIR = "Unpair";
    private static final String PAIRINGPORT_NAME = "pairing";  // Port name
    private static final int PAIRINGPORT_PRODUCTID = 0;        // Product ID

    // Pair a device via cloud (sends RPC to device via HSDP MQTT)
    // Parameters: clientId, clientSecret, ???
    public void pair(String p1, String p2, String p3, PairingCallback callback) {
        performRemoteMethodInvocation("Pair", createParams(p1, p2, p3), callback);
    }

    // Extended pair with more parameters
    public void pair(String p1, String p2, String p3, String p4, String p5,
                    String[] p6, PairingCallback callback) {
        performRemoteMethodInvocation("Pair", createParams(p1, p2, p3, p4, p5, p6), callback);
    }

    // Unpair a device via cloud
    public void unpair(String p1, String p2, String p3, String p4, PairingCallback callback) {
        performRemoteMethodInvocation("Unpair", createParams(p1, p2, p3, p4), callback);
    }

    // Uses execMethod on the communication strategy
    // This translates to an ExecMethod Condor operation over MQTT
    private void performRemoteMethodInvocation(String method, List<Object> params,
                                               PairingCallback callback) {
        execMethod(method, params, result -> {
            if (result instanceof FailureResult) { callback.onPairingResult(-3); return; }
            double value = ((Double) ((List) result.getValue()).get(0)).doubleValue();
            if (value == 0.0 || value == 1.0) callback.onPairingResult(0); // Success
            else callback.onPairingResult(-1); // Error
        });
    }
}
```

---

## 31. HSDP Authentication

**File:** `connectivity/condor/hsdp/HSDPAuthentication.java`

Full HSDP authentication flow: bootstrap -> provision -> sign on.

```java
public class HSDPAuthentication {
    private static final long ACCESS_TOKEN_CACHE_THRESHOLD_SECONDS = 120;
    private static final long ACCESS_TOKEN_INVALIDATION_WINDOW_SECONDS = 30;

    private final HSDPConfiguration configuration;
    private final IdentityAccessManagementServiceV2 iamService;
    private IdentityAccessManagementModel.TokenResponse provisionedTokenResponse;
    private Timer tokenExpirationTimer;
    Long provisionedTokenResponseExpiresOn = 0L;

    // Main sign-on flow:
    public void signOn(Callback callback) {
        if (configuration.getTokenSet() != null) {
            // Have cached tokens: apply refresh policy and discover services
            PassiveRefreshPolicy policy = new PassiveRefreshPolicy();
            HSDPTokenSet tokenSet = configuration.getTokenSet();
            policy.setAccessToken(tokenSet.getAccessToken());
            policy.setRefreshToken(tokenSet.getRefreshToken());
            policy.setSignedToken(tokenSet.getSignedToken());
            serviceFactory.applyRefreshPolicy(policy, true);
            discoverServices(callback);
        } else if (isProvisioningRequired()) {
            // No identity: bootstrap -> discover services -> provision -> sign on
            bootstrapSignOn(err -> {
                if (err == null) discoverServices(err2 -> {
                    if (err2 == null) provision(err3 -> {
                        if (err3 == null) signOn(callback); // Recursive: now has identity
                        else callback.complete(err3);
                    });
                    else callback.complete(err2);
                });
                else callback.complete(err);
            });
        } else {
            // Have identity but no tokens: provisioned sign-on
            provisionedSignOn(callback);
        }
    }

    // Bootstrap: get initial access token using bootstrap credentials
    private void bootstrapSignOn(Callback callback) {
        HSDPBootstrapCredentials creds = configuration.getBootstrapCredentials();
        iamService.getAccessToken(creds.getClientId(), creds.getClientSecret(), callback);
    }

    // Provisioned sign-on: use stored identity to get access + refresh tokens
    private void provisionedSignOn(Callback callback) {
        HSDPIdentity identity = configuration.getProvisionedIdentity();
        // Check if cached token is still valid (120s threshold)
        long remaining = provisionedTokenResponseExpiresOn - (System.currentTimeMillis() / 1000);
        if (provisionedTokenResponse != null && remaining > 120) {
            callback.complete(null); // Use cached
        } else {
            // Fetch new tokens using identity credentials
            iamService.getAccessToken(identity.getClientId(), identity.getClientSecret(),
                identity.getUsername(), identity.getPassword(), (tokenResponse, error) -> {
                    provisionedTokenResponse = tokenResponse;
                    // Compute absolute expiry time
                    provisionedTokenResponseExpiresOn = tokenResponse.getExpiresIn()
                        + (System.currentTimeMillis() / 1000);
                    persistAccessTokens(tokenResponse);
                    discoverServices(callback);
                });
        }
    }

    // Token expiration: schedule timer to refresh before expiry
    private void restartExpiryTimer(long expiresInSeconds) {
        if (expiresInSeconds - 30 < 0) {
            // Not enough time: report error
            return;
        }
        tokenExpirationTimer.cancel();
        tokenExpirationTimer = new Timer();
        tokenExpirationTimer.schedule(new TimerTask() {
            public void run() {
                if (!isTokenSetAvailable()) {
                    provisionedSignOn(callback); // Re-authenticate
                } else {
                    configuration.refreshAccessTokens(); // Use refresh token
                }
            }
        }, expiresInSeconds * 1000);
    }

    public String getAccessToken() { /* from tokenSet or provisionedTokenResponse */ }
    public String getSignedToken() { /* from tokenSet or provisionedTokenResponse */ }
}
```

---

## 32. HSDP Configuration

**File:** `connectivity/condor/hsdp/HSDPConfiguration.java`

Interface defining HSDP configuration with three data classes.

```java
public interface HSDPConfiguration {
    // Service endpoints
    String getBasePathForIAMService();        // IAM token endpoint
    String getBasePathForDiscoveryService();  // Service discovery endpoint

    // Bootstrap (app-level credentials for initial provisioning)
    HSDPBootstrapCredentials getBootstrapCredentials();
    // Returns: clientId + clientSecret for bootstrap sign-on

    // Provisioned identity (device-specific, stored after provisioning)
    HSDPIdentity getProvisionedIdentity();
    // Returns: clientId, clientSecret, username, password, hsdpIdentifier, identitySignature

    // Cached tokens
    HSDPTokenSet getTokenSet();
    // Returns: accessToken, refreshToken, signedToken, accessTokenExpiresIn

    // Persistence
    void persistIdentity(HSDPIdentity identity);
    void persistTokenSet(HSDPTokenSet tokenSet);
    void refreshAccessTokens();

    // Provisioning evidence (for first-time setup)
    Map<String, Object> getProvisioningEvidence();

    // Data classes:
    class HSDPBootstrapCredentials { String clientId, clientSecret; }
    class HSDPIdentity { String clientId, clientSecret, username, password, hsdpIdentifier, identitySignature; }
    class HSDPTokenSet { String accessToken, refreshToken, signedToken; long accessTokenExpiresIn; }
}
```

---

## 33. HSDP Command Queue

**File:** `connectivity/condor/hsdp/HSDPCommandQueue.java`

Wraps HSDPController to serialize MQTT commands via a semaphore.

```java
public class HSDPCommandQueue implements HSDPMessenger {
    private final HSDPMessenger hsdpMessenger; // The actual HSDPController
    private final ExecutorService queue;        // Single-thread executor
    private final Semaphore semaphore;          // Ensures one command at a time

    public void sendCommand(String hsdpId, ControlModel.Command command, Completable completable) {
        queue.submit(() -> {
            semaphore.acquire(); // Block until previous command completes
            hsdpMessenger.sendCommand(hsdpId, command, error -> {
                completable.onCompleted(error);
                semaphore.release(); // Allow next command
            });
        });
    }

    // connect, disconnect, register/unregister listeners: delegate directly
}
```

---

## 34. Error Codes (Complete List)

**File:** `connectivity/condor/core/request/Error.java`

```java
public enum Error {
    NO_ERROR("No Error", 0),
    NOT_UNDERSTOOD("Request not understood.", 1),
    REQUEST_FAILED("Failed to perform request."),        // no code
    INVALID_PARAMETER("Invalid parameter.", 12),
    NO_SUCH_METHOD("No such method.", 10),
    NO_SUCH_OPERATION("No such operation.", 7),
    NO_SUCH_PORT("No such port.", 3),
    NO_SUCH_PRODUCT("No such product.", 8),
    NO_SUCH_PROPERTY("No such property.", 6),
    NOT_IMPLEMENTED("Not implemented.", 4),
    NOT_SUBSCRIBED("Not subscribed.", 13),
    OUT_OF_MEMORY("Out of memory.", 2),
    PROPERTY_ALREADY_EXISTS("Property already exists.", 9),
    PROTOCOL_VIOLATION("Protocol violation.", 14),
    UNKNOWN("Unknown error.", 255),
    VERSION_NOT_SUPPORTED("Version not supported.", 5),
    WRONG_PARAMETERS("Wrong parameters.", 11),
    BUSY("Busy."),                                       // HTTP 429
    CANNOT_CONNECT("Cannot connect to appliance."),      // HTTP 502
    UNRECOVERABLE_CONNECTION("Unrecoverable connection."),
    SEND_FAILED("Command not sent."),                    // MQTT publish failed
    IOEXCEPTION("I/O exception occurred."),
    NO_REQUEST_DATA("Request cannot be performed with null or empty data."),
    NO_TRANSPORT_AVAILABLE("Request cannot be performed - No transport available."),
    NOT_CONNECTED("Request cannot be performed - Not connected to an appliance."),
    TIMED_OUT("Request timed out", 15),
    NOT_AVAILABLE("Communication not available."),
    INSECURE_CONNECTION("Connection is not secure."),    // SSL failure
    REQUEST_UNAUTHORIZED("Request is unauthorized."),    // HTTP 401
    REJECTED("HSDP rejected message."),                  // MQTT command rejected
    EMPTY_RESPONSE("Empty response body.");              // HTTP 200 but empty
}

---

## 35. ObservableCommunicationStrategy

**File:** `connectivity/condor/core/communication/ObservableCommunicationStrategy.java`

Base class for LAN and HSDP strategies. Manages availability listeners and subscription events.

```java
public abstract class ObservableCommunicationStrategy implements CommunicationStrategy {
    private final Set<AvailabilityListener<CommunicationStrategy>> availabilityListeners = new CopyOnWriteArraySet();
    protected final Set<SubscriptionEventListener> subscriptionEventListeners = new CopyOnWriteArraySet();

    // Convert response bytes to JSON string (default: UTF-8 decode)
    // LanCommunicationStrategy overrides this to add AES decryption
    public String processByteArrayToJsonString(byte[] data) {
        return data == null ? null : new String(data, StandardCharsets.UTF_8);
    }

    // Build unsubscription payload: {"subscriber": "<appId>"}
    public Map<String, Object> getUnsubscriptionData() {
        HashMap map = new HashMap();
        map.put("subscriber", CondorEntryPoint.getAppIdProvider().getAppId());
        return map;
    }

    // Build subscription payload: {"subscriber": "<appId>", "ttl": <ttl>}
    public Map<String, Object> getSubscriptionData(int ttl) {
        Map<String, Object> map = getUnsubscriptionData();
        map.put("ttl", ttl);
        return map;
    }
}
```

---

## 36. NullCommunicationStrategy

**File:** `connectivity/condor/core/communication/NullCommunicationStrategy.java`

Fallback when no transport is available. All operations return `Error.NOT_CONNECTED`.

```java
public class NullCommunicationStrategy extends ObservableCommunicationStrategy {
    // isAvailable() returns true (so CombinedStrategy doesn't reject it)
    // but all operations (get/put/subscribe/etc.) immediately return NOT_CONNECTED error
    // processByteArrayToJsonString returns null
    // TTL is 300 seconds (5 minutes)
}
```

---

## 37. TransportContext Interface

**File:** `connectivity/condor/core/context/TransportContext.java`

```java
public interface TransportContext {
    CommunicationStrategy createCommunicationStrategyFor(NetworkNode networkNode);
    DiscoveryStrategy getDiscoveryStrategy();
    void registerTagger(GenericTagger tagger);   // @Deprecated
    void unregisterTagger(GenericTagger tagger); // @Deprecated
}
```

Implemented by `LanTransportContext` and `HSDPTransportContext`.

---

## 38. RuntimeConfiguration

**File:** `connectivity/condor/core/configuration/RuntimeConfiguration.java`

Simple holder for Android Context and optional secure DatabaseHelper.

```java
public class RuntimeConfiguration {
    private final Context context;
    private final DatabaseHelper secureDatabaseHelper;
    public RuntimeConfiguration(Context context, DatabaseHelper databaseHelper) { ... }
}
```

---

## 39. DeviceCache and CacheData

**Files:** `connectivity/condor/core/devicecache/DeviceCache.java`, `CacheData.java`

TTL-based device cache used by discovery strategies.

```java
// DeviceCache: ConcurrentHashMap<cppId, CacheData> with expiration callbacks
public class DeviceCache {
    private final Map<String, CacheData> data = new ConcurrentHashMap();
    private final ScheduledExecutorService executor = Executors.newSingleThreadScheduledExecutor();

    // Add device with TTL - fires ExpirationCallback when TTL expires
    public void add(NetworkNode node, ExpirationCallback callback, long ttlMillis) {
        add(new CacheData(executor, callback, ttlMillis, node));
    }

    // Get cached data by cppId
    public CacheData getCacheData(String cppId) { return data.get(cppId); }

    // Remove and stop timer
    public CacheData remove(String cppId) { ... }

    // Clear all - returns removed entries (so caller can fire "lost" events)
    public synchronized Collection<CacheData> clear() { ... }

    interface ExpirationCallback { void onCacheExpired(NetworkNode node); }
}

// CacheData: wraps NetworkNode with a ScheduledFuture timer
public class CacheData {
    private final NetworkNode networkNode;
    private final long expirationPeriodMillis;
    private ScheduledFuture<Void> future;

    // On construction, starts a timer that fires ExpirationCallback after TTL
    public CacheData(ScheduledExecutorService executor, ExpirationCallback callback,
                    long ttlMillis, NetworkNode node) {
        startTimer(); // schedule(expirationTask, ttlMillis, MILLISECONDS)
    }

    public void resetTimer() { stopTimer(); startTimer(); } // Re-discovered: reset TTL
    public void stopTimer() { future.cancel(true); }        // Removed from cache
}
```

---

## 40. Request Queue (Threading Model)

**File:** `connectivity/condor/core/request/RequestQueue.java`

Serial request execution on a dedicated HandlerThread.

```java
public class RequestQueue {
    private Handler mRequestHandler;              // Background thread handler
    private final Handler responseHandler;        // Main thread handler for callbacks
    private final ArrayList<Request> threadNotYetStartedQueue; // Queued before thread starts

    public RequestQueue() {
        // Creates a HandlerThread, waits for looper, then initializes mRequestHandler
        initializeRequestThread();
        this.responseHandler = HandlerProvider.createHandler(); // Main thread
    }

    // Add request to end of queue
    public synchronized void addRequest(Request request) {
        if (mRequestHandler == null) {
            threadNotYetStartedQueue.add(request); // Thread not ready yet
        } else {
            postRequestOnBackgroundThread(request);
        }
    }

    // Priority insert (key exchange goes first)
    public synchronized void addRequestInFrontOfQueue(Request request) {
        mRequestHandler.postAtFrontOfQueue(() -> {
            Response response = request.execute();
            postResponseOnUiThread(request, response);
        });
    }

    // Execute on background thread, deliver response on main thread
    private void postRequestOnBackgroundThread(Request request) {
        mRequestHandler.post(() -> {
            Response response = request.execute(); // Blocking HTTP/MQTT call
            postResponseOnUiThread(request, response);
        });
    }

    private void postResponseOnUiThread(Request request, Response response) {
        responseHandler.post(() -> request.notifyResponseHandler(response));
    }
}
```

---

## 41. Result Type (Success/Failure)

**File:** `connectivity/condor/core/port/Result.java`

Sealed class for port operation results.

```java
public abstract class Result<T> {
    public Error getError() { return null; }     // Override in FailureResult
    public String getErrorData() { return null; } // Override in FailureResult
    public T getValue() { return null; }          // Override in SuccessResult

    public static final class SuccessResult<T> extends Result<T> {
        private final T value;
        public T getValue() { return value; }
    }

    public static final class FailureResult<T> extends Result<T> {
        private final Error error;
        private final String errorData;
        public Error getError() { return error; }
        public String getErrorData() { return errorData; }
    }
}
```

---

## 42. CondorPortApi Interface

**File:** `connectivity/condor/core/port/CondorPortApi.java`

The public API for interacting with device ports.

```java
public interface CondorPortApi<P extends CondorPortProperties> {
    void getProperties(Consumer<Result<P>> callback);
    void putProperties(P properties, Consumer<Result<P>> callback);
    void subscribe(Consumer<Result<P>> callback);
    void unsubscribe(Consumer<Result<P>> callback);
    void execMethod(String methodName, List<?> params, Consumer<Result<List<Object>>> callback);
    void addSubscriptionListener(PortSubscriptionListener<P> listener);
    void removeSubscriptionListener(PortSubscriptionListener<P> listener);
    P getCachedProperties();
}
```

---

## 43. PortSubscriptionListener

**File:** `connectivity/condor/core/port/PortSubscriptionListener.java`

```java
public interface PortSubscriptionListener<P extends CondorPortProperties> {
    void onPortSubscriptionEvent(CondorPort<P> port);                  // New data received
    void onPortSubscriptionEnded(CondorPort<P> port, Error error, String errorData); // Subscription lost
}
```

---

## 44. DevicePort and DevicePortProperties

**Files:** `connectivity/condor/core/port/common/DevicePort.java`, `DevicePortProperties.java`

Default port on every appliance: reads device info at `/di/v1/products/1/device`.

```java
public class DevicePort extends CondorPort<DevicePortProperties> {
    public String getCondorPortName() { return "device"; }
    public int getCondorProductId() { return 1; }
    public void setDeviceName(String name) { /* PUT {"name": name} */ }
}

public class DevicePortProperties implements CondorPortProperties {
    @SerializedName("name")           String name;           // Friendly name
    @SerializedName("type")           String type;           // Device type
    @SerializedName("modelid")        String modelid;        // Model ID
    @SerializedName("modelname")      String modelName;      // Model name
    @SerializedName("productname")    String productName;    // Product name
    @SerializedName("productversion") String productVersion; // Product version
    @SerializedName("serial")         String serial;         // Serial number
    @SerializedName("ctn")            String ctn;            // Commercial type number
    @SerializedName("udi")            String udi;            // Unique device identifier
    @SerializedName("swversion")      String swVersion;      // Software version
    @SerializedName("wificountry")    String wifiCountry;    // WiFi country code
    @SerializedName("allowuploads")   Boolean mAllowUploads; // Allow firmware uploads
    Boolean mAllowPairing;                                    // Allow pairing
}
```

---

## 45. HsdpPairingPort

**File:** `connectivity/condor/core/port/common/HsdpPairingPort.java`

Cloud pairing via HSDP MQTT RPC. Port name: `"pairing"`, product ID: `0`.

```java
public class HsdpPairingPort extends CondorPort<HsdpPairingPortProperties> {
    // pair(hsdpId, secret) -> ExecMethod("Pair", [hsdpId, secret])
    public void pair(String p1, String p2, PairingCallback callback) {
        execMethod("Pair", [p1, p2], result -> {
            // Success if first return value == 0.0
            if (result.getValue().get(0) == 0.0) callback.onPairingResult(0);
            else callback.onPairingResult(-1);
        });
    }

    // unpair(hsdpId, secret) -> ExecMethod("Unpair", [hsdpId, secret])
    public void unpair(String p1, String p2, PairingCallback callback) { ... }
}
```

---

## 46. BinaryCondorPort

**File:** `connectivity/condor/core/port/BinaryCondorPort.java`

Port for binary (non-JSON) data. Port name must end with `.b`.

```java
public class BinaryCondorPort extends CondorPort<BinaryCondorPortProperties> {
    // Stores raw byte[] in BinaryCondorPortProperties instead of parsing JSON
    public boolean processResponse(byte[] data) {
        setPortProperties(new BinaryCondorPortProperties(data));
        return true;
    }
    // propertiesToMap returns {"binary_condor_port_properties_key": byte[]}
    // BinaryCondorPortProperties.KEY = "binary_condor_port_properties_key"
    // execMethod is NOT_IMPLEMENTED
}
```

---

## 47. ConnectivityMonitor

**File:** `connectivity/condor/core/util/ConnectivityMonitor.java`

Android network monitoring with factory methods for different network types.

```java
public class ConnectivityMonitor implements Availability<ConnectivityMonitor> {
    private boolean isConnected;
    private final int[] allowedTransports;            // e.g., [WIFI, ETHERNET]
    private final int[] requiredNetworkCapabilities;  // e.g., [NET_CAPABILITY_VALIDATED]

    // Factory: LAN-only (WiFi + Ethernet transports)
    public static ConnectivityMonitor forNetworkTransportLAN(Context context) {
        return new ConnectivityMonitor(context, new int[0], new int[]{3/*ETHERNET*/, 1/*WIFI*/});
    }

    // Factory: Internet (any transport with NET_CAPABILITY_VALIDATED)
    public static ConnectivityMonitor forNetworkCapabilityInternet(Context context) {
        return new ConnectivityMonitor(context, new int[]{16/*VALIDATED*/}, new int[0]);
    }

    // Registers Android NetworkCallback that tracks:
    // - onCapabilitiesChanged: check required capabilities + transports
    // - onLosing/onLost/onUnavailable: mark disconnected
    // Notifies all AvailabilityListeners on state change
}
```

---

## 48. Poller (Periodic Polling)

**File:** `connectivity/condor/core/util/Poller.java`

Periodic getProperties() polling with timeout.

```java
public class Poller<P extends CondorPortProperties> implements Runnable {
    private final CondorPort<P> port;
    private final long intervalMillis;   // Polling interval
    private final long endTime;          // Absolute time when polling stops
    private final Listener<P> listener;  // Callback for events + timeout
    private final ScheduledExecutorService executor;
    private boolean isPolling;

    public interface Listener<P> {
        void onEvent(P properties);  // Called with each poll result
        void onTimedOut();           // Called when endTime is reached
    }

    public void start() {
        future = executor.scheduleAtFixedRate(this, 0, intervalMillis, MILLISECONDS);
        isPolling = true;
    }

    @Override
    public void run() {
        if (currentTimeMillis() > endTime) { timeOut(); return; }
        CountDownLatch latch = new CountDownLatch(1);
        port.getProperties(result -> {
            if (isPolling) {
                if (result instanceof SuccessResult) listener.onEvent(result.getValue());
            }
            latch.countDown();
        });
        latch.await(intervalMillis, MILLISECONDS); // Block until response or next poll
    }

    public void stop() {
        executor.shutdownNow();
        isPolling = false;
    }
}
```

---

## 49. AppIdProvider

**File:** `connectivity/condor/core/util/AppIdProvider.java`

Generates a random app instance ID used in subscription requests.

```java
public class AppIdProvider {
    // Format: "deadbeef" + 8 random hex digits
    private String appId = String.format("deadbeef%08x", new SecureRandom().nextInt());

    public String getAppId() { return appId; }
    public void setAppId(String id) { appId = id; notifyListeners(); }
}
```

---

## 50. GsonProvider

**File:** `connectivity/condor/core/util/GsonProvider.java`

Singleton Gson instance configured to serialize nulls and disable HTML escaping.

```java
public final class GsonProvider {
    public static final String EMPTY_JSON_OBJECT_STRING; // "{}"
    private static final Gson INSTANCE = new GsonBuilder()
        .serializeNulls()
        .disableHtmlEscaping()
        .create();

    public static Gson get() { return INSTANCE; }
}
```

---

## 51. SubscriptionEventListener and SubscriptionHandler

**Files:** `core/subscription/SubscriptionEventListener.java`, `SubscriptionHandler.java`

```java
public interface SubscriptionEventListener {
    void onSubscriptionEventReceived(String portName, byte[] data);
    void onSubscriptionEventDecryptionFailed(String portName);
}

public abstract class SubscriptionHandler {
    // Posts events to UI thread
    public abstract void enableSubscription(NetworkNode node, Set<SubscriptionEventListener> listeners);
    public abstract void disableSubscription();

    // Helper: post event to main thread
    protected void postSubscriptionEventOnUiThread(String port, byte[] data,
            Set<SubscriptionEventListener> listeners) {
        handler.post(() -> {
            for (SubscriptionEventListener l : listeners) l.onSubscriptionEventReceived(port, data);
        });
    }
}
```

---

## 52. DiscoveryStrategy Interface

**File:** `connectivity/condor/core/discovery/DiscoveryStrategy.java`

```java
public interface DiscoveryStrategy {
    void start();
    void start(Set<String> modelIds); // Filter by model ID
    void stop();
    void clearDiscoveredNetworkNodes();
    void addDiscoveryListener(DiscoveryListener listener);
    void removeDiscoveryListener(DiscoveryListener listener);

    interface DiscoveryListener {
        default void onDiscoveryStarted() {}
        default void onDiscoveryStopped() {}
        default void onDiscoveryError(DiscoveryException e) {}
        default void onNetworkNodeDiscovered(NetworkNode node) {}
        default void onNetworkNodeLost(NetworkNode node) {}
    }
}
```

---

## 53. ObservableDiscoveryStrategy

**File:** `connectivity/condor/core/discovery/ObservableDiscoveryStrategy.java`

Base class that posts discovery events to main thread via Handler.

```java
public abstract class ObservableDiscoveryStrategy implements DiscoveryStrategy {
    private final Handler responseHandler = HandlerProvider.createHandler(); // Main thread
    private final Set<DiscoveryListener> discoveryListeners = new CopyOnWriteArraySet();

    // All notify methods post to main thread:
    protected void notifyNetworkNodeDiscovered(NetworkNode node) {
        for (DiscoveryListener l : discoveryListeners) {
            responseHandler.post(() -> l.onNetworkNodeDiscovered(node));
        }
    }
    // Same pattern for: notifyNetworkNodeLost, notifyDiscoveryStarted,
    // notifyDiscoveryStopped, notifyDiscoveryError
}
```

---

## 54. DiscoveredLanDevice

**File:** `connectivity/condor/lan/discovery/DiscoveredLanDevice.java`

Abstract data class populated by SSDP or mDNS discovery.

```java
public abstract class DiscoveredLanDevice {
    protected String bootId;          // Device boot counter
    protected String cppId;           // Unique device ID
    protected String deviceType;      // Device type string
    protected long expirationPeriod;  // TTL in milliseconds
    protected String friendlyName;    // Display name
    protected String ipAddress;       // IP address
    protected String manufacturer;    // "Philips"
    protected String modelName;       // Model name (e.g., "AC2729")
    protected String modelNumber;     // Model number
    protected String serialNumber;    // Serial number
    // + manufacturer URL, model URL, model description, presentation URL
}
```

---

## 55. DiscoveredDeviceListener and DiscoveryMechanism

**Files:** `connectivity/condor/lan/discovery/`

```java
public interface DiscoveredDeviceListener {
    void onDeviceAvailable(DiscoveredLanDevice device);
    void onDeviceUnavailable(DiscoveredLanDevice device);
}

public interface DiscoveryMechanism {
    void start() throws TransportUnavailableException;
    void stop();
    boolean isDiscovering();
    void addDeviceListener(DiscoveredDeviceListener listener);
    void removeDeviceListener(DiscoveredDeviceListener listener);
}
```

---

## 56. UDP Event Receiver (Singleton)

**File:** `connectivity/condor/lan/subscription/UdpEventReceiver.java`

Global singleton managing the UDP receiving thread. Multiple `LocalSubscriptionHandler` instances share it.

```java
public class UdpEventReceiver {
    private static UdpEventReceiver INSTANCE = null; // Lazy singleton
    private UdpReceivingThread udpReceivingThread;
    private final Set<UdpEventListener> udpEventListeners = new CopyOnWriteArraySet();

    // Start receiving: creates thread if needed, adds listener
    public int startReceivingEvents(UdpEventListener listener) {
        int port = startUdpThreadIfNecessary();
        addUdpEventListener(listener);
        return port; // Returns the bound UDP port number
    }

    // Stop receiving: removes listener, stops thread if no listeners left
    public void stopReceivingEvents(UdpEventListener listener) {
        removeUdpEventListener(listener);
        if (udpEventListeners.isEmpty()) {
            udpReceivingThread.stopThread();
            udpReceivingThread = null;
        }
    }

    // Create thread, wait for socket setup, return bound port
    private synchronized int startUdpThreadIfNecessary() {
        if (udpReceivingThread != null) return udpReceivingThread.getActualBoundUdpPort();
        CountDownLatch latch = new CountDownLatch(1);
        udpReceivingThread = new UdpReceivingThread(eventListener, latch);
        udpReceivingThread.start();
        latch.await(); // Wait for socket to bind
        return udpReceivingThread.getActualBoundUdpPort();
    }
}
```

---

## 57. UDP Receiving Thread

**File:** `connectivity/condor/lan/subscription/UdpReceivingThread.java`

Dedicated thread that listens for UDP datagrams from devices.

```java
public class UdpReceivingThread extends Thread {
    private static final int UDP_PORT = 8080;    // Default port, fallback to OS-assigned
    private int actualBoundUdpPort = -1;
    private DatagramSocket socket;
    private boolean stop;

    public void run() {
        acquireMulticastLock();
        setupSocket();
        while (!stop) {
            receiveDatagram(socket);
        }
        // cleanup
    }

    public void setupSocket() {
        socket = new DatagramSocket(null);
        socket.setReuseAddress(true);
        try {
            socket.bind(new InetSocketAddress(8080)); // Try port 8080 first
        } catch (SocketException e) {
            socket.close();
            socket = new DatagramSocket(null);
            socket.bind(null); // Fallback: OS assigns random port
        }
        actualBoundUdpPort = socket.getLocalPort();
        socketSetupLatch.countDown(); // Signal that socket is ready
    }

    public void receiveDatagram(DatagramSocket socket) {
        DatagramPacket packet = new DatagramPacket(new byte[1024], 1024);
        socket.receive(packet); // Blocking

        String data = new String(packet.getData()).trim();
        String[] lines = data.split("\n");
        // lines[0] = HTTP-style request line: "NOTIFY /di/v1/products/{id}/{port} HTTP/1.1"
        // lines[last] = encrypted payload (base64 encoded AES ciphertext)

        String senderIP = packet.getAddress().getHostAddress();
        String portName = parseRequestHeaderLine(lines[0]); // Extract port path
        String payload = lines[lines.length - 1];            // Last line = encrypted data

        listener.onUDPEventReceived(payload, portName, senderIP);
    }

    // Parse "NOTIFY /di/v1/products/1/air HTTP/1.1" -> "air"
    private String parseRequestHeaderLine(String line) {
        String[] parts = line.split(" ")[1].split("/");
        // Skip first 4 segments (/di/v1/products/1/), join the rest
        StringBuilder sb = new StringBuilder();
        for (int i = 4; i < parts.length; i++) {
            sb.append(parts[i]);
            if (i < parts.length - 1) sb.append("/");
        }
        return sb.toString();
    }
}
```

**UDP packet format (from device):**
```
NOTIFY /di/v1/products/{productId}/{portName} HTTP/1.1\n
...\n
<base64_aes_encrypted_json_payload>
```

---

## 58. SsidProvider

**File:** `connectivity/condor/lan/util/SsidProvider.java`

Tracks the current WiFi SSID and notifies listeners on network changes.

```java
public class SsidProvider {
    private String currentSsid;
    private final WifiManager wifiManager;

    public SsidProvider(Context context) {
        wifiManager = (WifiManager) context.getApplicationContext().getSystemService("wifi");
        currentSsid = getCurrentSsid();
        // Register default network callback to detect SSID changes
        connectivityManager.registerDefaultNetworkCallback(new NetworkCallback() {
            public void onAvailable(Network network) {
                String newSsid = getCurrentSsid();
                if (!Objects.equals(newSsid, currentSsid)) {
                    currentSsid = newSsid;
                    notifyListeners();
                }
            }
        });
    }

    public String getCurrentSsid() {
        WifiInfo info = wifiManager.getConnectionInfo();
        if (info == null || info.getSupplicantState() != SupplicantState.COMPLETED) return null;
        return info.getSSID();
    }
}
```

---

## 59. DatabaseHelper Interface

**File:** `connectivity/condor/core/store/DatabaseHelper.java`

```java
public interface DatabaseHelper {
    long insertRow(ContentValues values) throws SQLException;
    Cursor query(String selection, String[] selectionArgs) throws SQLException;
    int delete(String cppId) throws SQLException;
    void close();
}
```

---

## 60. HSDP Error Enums

**Files:** `connectivity/condor/hsdp/HSDPControlConnectionError.java`, `HSDPAuthenticationError.java`

```java
// Connection-level errors (MQTT)
public enum HSDPControlConnectionError {
    AUTHENTICATION_FAILED,            // Auth error (wraps HSDPAuthenticationError)
    INTERRUPTED,                      // Thread interrupted
    NO_CONTROL_SERVICE_AVAILABLE,     // Control service not found via discovery
    MISSING_ACCESS_TOKEN,             // No access token
    MISSING_SIGNED_TOKEN,             // No signed token
    CONNECT_FAILED,                   // MQTT connect failed
    SEND_FAILED;                      // MQTT publish failed

    HSDPAuthenticationError authenticationError; // Wrapped auth error
    ClientError underlyingError;                 // Wrapped HSDP client error
}

// Authentication-level errors
public enum HSDPAuthenticationError {
    MISSING_BOOTSTRAP_IDENTITY,       // No bootstrap credentials
    BOOTSTRAP_SIGN_ON_FAILED,         // Bootstrap auth failed
    SERVICE_DISCOVERY_FAILED,         // Cannot discover HSDP services
    MISSING_PROVISIONING_URL,         // PRV service URL not found
    MISSING_PROVISIONING_EVIDENCE,    // No provisioning evidence
    PROVISIONING_FAILED,              // Provisioning API failed
    INSUFFICIENT_IDENTITY_INFORMATION,// Missing required identity fields
    PROVISIONED_SIGN_ON_FAILED,       // Token fetch with identity failed
    MISSING_TOKEN_RESPONSE,           // Null token response
    TOKEN_EXPIRY_TOO_SHORT;           // Token expires in <30s

    ClientError underlyingError;
}
```

---

## 61. HSDP Remote Subscription Handler

**File:** `connectivity/condor/hsdp/HSDPRemoteSubscriptionHandler.java`

Handles push notifications from HSDP MQTT (equivalent of UDP for cloud).

```java
public class HSDPRemoteSubscriptionHandler extends SubscriptionHandler implements HSDPMessageListener {
    String hsdpId;
    HSDPMessenger messenger;

    public void enableSubscription(NetworkNode node, Set<SubscriptionEventListener> listeners) {
        this.hsdpId = node.getHsdpId();
        this.subscriptionEventListeners = listeners;
        messenger.registerMessageListener(this); // Listen for MQTT messages
    }

    public void disableSubscription() {
        messenger.unregisterMessageListener(this);
    }

    // When MQTT "notification" with CondorOperation.CHANGE_INDICATION arrives:
    public void messageReceived(ControlModel.Received received) {
        if (!"notification".equalsIgnoreCase(received.getType())) return;
        ControlModel.Command cmd = received.getCommand();
        if (cmd != null && cmd.getStatusDetail() != null) {
            String op = cmd.getStatusDetail().get("op");
            if (CondorOperation.CHANGE_INDICATION.equals(CondorOperation.fromString(op))) {
                CondorControlMessage msg = Gson.fromJson(cmd.getStatusDetailAsJsonString(), CondorControlMessage.class);
                // Forward to subscription listeners
                postSubscriptionEventOnUiThread(msg.path, Gson.toJson(msg.values).getBytes(UTF_8), listeners);
            }
        }
    }
}
```

---

## 62. Condor Message Keys

**File:** `connectivity/condor/hsdp/messages/CondorKey.java`

```java
public enum CondorKey {
    VERSION("condorVersion"),  // Protocol version (always "1")
    OPERATION("op"),           // Operation name (GetProps, PutProps, etc.)
    PATH("path"),              // Target path ("{productId}/{portName}")
    VALUES("values"),          // Request/response data
    TIME_TO_LIVE("ttl"),       // Subscription TTL in seconds
    UNKNOWN("unknown");
}
```

---

## 63. DaConnect Authentication Interfaces

**Files:** `cl/daconnect/authentication/`

```java
// Provides access tokens (Gigya/HSDP) - async via RxJava Single
public interface ClientAuthenticationProvider {
    Single<AccessToken> getAccessToken();
    Single<IdToken> getIdToken();
}

// Synchronous token access
public interface TokenProvider {
    String getAccessToken(); // Returns raw token string
    String getIdToken();     // Returns raw ID token string
}

// Cloud auth service - federated user identity
public interface DaIoTAuthenticationService {
    Single<UserId> federatedUserId(); // Get user's federated ID
    Completable clearUserData();       // Logout
}

// MQTT credential provider
public interface DaIoTCredentialsProvider {
    Single<UserId> getUserId();
    Single<MqttConnectionInfo> getMqttConnectionInfo(); // Get MQTT credentials
    void invalidateMqttConnectionInfo();                 // Force credential refresh
}

// User auth states
public enum UserAuthenticationState {
    SIGNED_IN,
    SIGNED_OUT,
    SIGNED_OUT_FEDERATED_TOKEN_INVALID,
    UNKNOWN
}
```

---

## 64. DaConnect Authentication Models

**Files:** `cl/daconnect/authentication/models/`

```java
// AccessToken: inline class wrapping a String value
public final class AccessToken { private final String value; }

// MqttSignature: inline class wrapping a String value
public final class MqttSignature { private final String value; }

// IdToken: inline class wrapping a String value (not shown, same pattern)

// MqttConnectionInfo: data class with all MQTT connection parameters
public final class MqttConnectionInfo {
    private final String accessToken;       // HSDP access token
    private final String mqttSignature;     // Auth signature for Custom Authorizer
    private final String tenant;            // HSDP tenant identifier
    private final WebSocketUrl webSocketUrl; // wss:// endpoint URL
}
```

---

## 65. All Port Types Reference

Complete list of all Condor port types defined in the APK:

| Port Class | Port Name | Product ID | Purpose |
|------------|-----------|-----------|---------|
| `DevicePort` | `"device"` | 1 | Device info (name, model, firmware version) |
| `SecurityPort` | `"security"` | 0 | Encryption key exchange |
| `DeviceCloudPairingPort` | `"pairing"` | 0 | Cloud pairing via HSDP |
| `HsdpPairingPort` | `"pairing"` | 0 | HSDP pairing (simplified) |
| `FirmwarePort` | `"firmware"` | 0 | Firmware version info + update |
| `WifiPort` | `"wifi"` | 0 | WiFi configuration |
| `WifiNetworksPort` | `"wifinetworks"` | 0 | Available WiFi networks scan |
| `WifiUiPort` | `"wifiui"` | 0 | WiFi UI state |
| `TimePort` | `"time"` | 0 | Device time |
| `LocalePort` | `"locale"` | 0 | Device locale settings |
| `LogPort` | `"log"` | 0 | Device logs |
| `LogSettingsPort` | `"logsettings"` | 0 | Log configuration |
| `BackendPort` | `"backend"` | 0 | Backend connectivity settings |
| `TransportPort` | `"transport"` | 0 | Transport layer settings |
| `FacPort` | `"fac"` | 0 | Factory settings |
| `BleParamsPort` | `"bleparams"` | 0 | Bluetooth parameters |
| `BinaryCondorPort` | `*.b` | variable | Raw binary data (non-JSON) |

Each port extends `CondorPort<P>` with a specific `CondorPortProperties` subclass that defines the JSON field mappings for that port's data.

---

## 66. HsdpPairingHandler (Full Pairing Flow)

**File:** `connectivity/condor/core/port/common/HsdpPairingHandler.java`

Orchestrates the full HSDP cloud pairing flow with 30s watchdog timeout.

```java
public class HsdpPairingHandler {
    private static final long WATCHDOG_TIMEOUT_MILLIS = 30000;
    private final HsdpPairingPort port;

    enum PairingFlowType { PAIR, UNPAIR }

    // Full flow:
    // 1. Subscribe to pairing port for ChangeIndications
    // 2. Execute Pair/Unpair RPC
    // 3. Wait for ChangeIndication confirming result (30s timeout)
    private void performPairingFlowWithType(PairingFlowType type, String pairingType,
                                            String trustee, Callback callback) {
        port.addSubscriptionListener(new HsdpPairingPortListener(pairingType, trustee, callback));
        port.subscribe(result -> {
            if (result instanceof FailureResult) { completePairingFlow(callback, result.getError()); return; }
            isSubscribeRequested = false;
            PairingCallback pc = pairingResult -> {
                if (pairingResult == 0) {
                    // Start 30s watchdog
                    watchdog = () -> completePairingFlow(callback, Error.TIMED_OUT);
                    handler.postDelayed(watchdog, 30000);
                } else {
                    completePairingFlow(callback, Error.REJECTED);
                }
            };
            if (type == PAIR) port.pair(pairingType, trustee, pc);
            else port.unpair(pairingType, trustee, pc);
        });
    }

    // ChangeIndication listener checks previousType + previousTrustee + previousResult
    private void handlePairingChangeIndication(HsdpPairingPortProperties props,
            String type, String trustee, Callback callback) {
        if (type.equals(props.getPreviousType()) && trustee.equals(props.getPreviousTrustee())) {
            if (props.isPreviousOperationSuccessful())
                completePairingFlow(callback, null);
            else
                completePairingFlow(callback, Error.REJECTED);
        }
    }
}
```

---

## 67. HsdpPairingPortProperties

**File:** `connectivity/condor/core/port/common/HsdpPairingPortProperties.java`

```java
public final class HsdpPairingPortProperties implements CondorPortProperties {
    @SerializedName("hsdpid")          String hsdpId;
    @SerializedName("previousaction")  Action previousAction;  // PAIR or UNPAIR
    @SerializedName("previousresult")  Boolean previousResult; // true = success
    @SerializedName("previoustrustee") String previousTrustee;
    @SerializedName("previoustype")    String previousType;    // e.g. "control"
    String semantics;

    enum Action {
        @SerializedName("Pair")   PAIR,
        @SerializedName("Unpair") UNPAIR
    }
}
```

---

## 68. FirmwarePortProperties

**File:** `connectivity/condor/core/port/firmware/FirmwarePortProperties.java`

```java
public final class FirmwarePortProperties implements CondorPortProperties {
    byte[] data;                                    // Firmware binary
    @SerializedName("mandatory")    Boolean isMandatory;
    @SerializedName("candownload")  Boolean mCanDownload;
    @SerializedName("canupgrade")   Boolean mCanUpgrade;
    @SerializedName("state")        FirmwarePortState mState;
    @SerializedName("maxchunksize") Integer maxChunkSize;
    String name;                                    // Firmware file name
    Integer progress;                               // 0-100
    Integer size;                                   // Bytes
    @SerializedName("statusmsg")    String statusMessage;
    String upgrade;                                 // Available version
    String version;                                 // Current version
    Map<String, String> versions;                   // Component versions

    enum FirmwarePortState {
        @SerializedName("idle")        IDLE,
        @SerializedName("preparing")   PREPARING,
        @SerializedName("downloading") DOWNLOADING,
        @SerializedName("checking")    CHECKING,
        @SerializedName("ready")       READY,
        @SerializedName(value="go", alternate={"programming"}) PROGRAMMING,
        @SerializedName(value="cancel", alternate={"canceling","cancelling"}) CANCELING,
        @SerializedName("error")       ERROR,
        @SerializedName("unknown")     UNKNOWN
    }

    public boolean isUpdateAvailable() { return !TextUtils.isEmpty(upgrade); }
}
```

---

## 69. HSDPController (Full)

**File:** `connectivity/condor/hsdp/HSDPController.java`

```java
// connect(): signOn() -> createControlService() -> controlServiceV1.connect(accessToken, signedToken)
// createControlService(): finds "IOT" tag in discovered services, extracts wss:// URL + topic prefix
// sendCommand(): if connected, send directly; otherwise connect first then send
// disconnect(): cancel token timer, controlServiceV1.disconnect()
// Auto-reconnect on token refresh via AuthenticationListener
```

---

## 70. HSDPControlPairingHandlerImpl

**File:** `connectivity/condor/hsdp/HSDPControlPairingHandlerImpl.java`

```java
// performPair(): hsdpPairingHandler.performPairingFlow("control", hsdpIdentifier, ...)
//   On success: reads hsdpId from pairing port -> stores in networkNode.setHsdpId()
// performUnpair(): hsdpPairingHandler.performUnpairingFlow("control", hsdpIdentifier, ...)
//   On success: networkNode.setHsdpId(null)
```

---

## 71-79. Remaining Port Properties and Utilities

### All Port Properties JSON Schemas

| Port | JSON Fields |
|------|-------------|
| **BackendPort** `"backend"` | Backend server config (~198 lines of fields) |
| **BleParamsPort** `"bleparams"` | BLE advertisement/connection params (~89 lines) |
| **LocalePort** `"locale"` | `locale` string |
| **LogPort** `"log"` | Device log entries |
| **LogSettingsPort** `"logsettings"` | Log level config (~73 lines) |
| **TimePort** `"time"` | `utc` (Long), `zone` (String) (~69 lines) |
| **TransportPort** `"transport"` | Transport status (~46 lines) |
| **WifiPort** `"wifi"` | `ssid`, `password`, `security`, `ip`, `gateway`, `netmask`, `dhcp` (~106 lines) |
| **WifiNetworksPort** `"wifinetworks"` | Scanned WiFi list |
| **WifiUiPort** `"wifiui"` | WiFi setup UI state (~71 lines) |
| **FacPort** `"fac"` | Factory settings (~49 lines) |

### Utility Classes (All Documented)

| Class | Purpose |
|-------|---------|
| `HandlerProvider` | Creates Android Handlers (main thread / specific Looper) |
| `Base64Adapter` | Gson TypeAdapter for byte[] <-> Base64 |
| `IntegerPreservingMapDeserializer` | Prevents Gson converting ints to doubles |
| `NoPrimitivesValidatorKt` | Validates port properties don't use primitives |
| `IOUtil` | Stream copy for firmware uploads |
| `IPProvider` | Local IP from WifiManager |
| `MulticastLockControlPoint` | Android multicast lock for mDNS/SSDP |
| `VerboseExecutor` | Debug ThreadPoolExecutor |
| `StrictModeConsts` | Traffic stats tag: 4242 |
| `GenericTagger` | Deprecated analytics tagging |
| `PairingListener` | `onPairingError`/`onPairingSucceeded` |

### Store Layer

| Class | Purpose |
|-------|---------|
| `NetworkNodeDatabaseFactory` | Creates DB with secure or non-secure helper |
| `NonSecureNetworkNodeDatabaseHelper` | SQLiteOpenHelper for `network_node.db` |
| `ApplianceDatabase` | Interface: save/delete/load appliance data |
| `NullApplianceDatabase` | No-op implementation |
| `DatabaseFetcher` | Interface for DB creation |
| `DatabaseHelper` | Interface: insertRow/query/delete/close |

### Discovery Remaining

| Class | Purpose |
|-------|---------|
| `DiscoverInfo` | Data holder for discovered device info (~50 lines) |
| `CppDiscoverEventListener` | `onDiscovered(DiscoverInfo)` |
| `DiscoveryEventListener` | `onDiscoveryException(Exception)` |
| `DiscoveryException` | Error codes 111 (transport), 112 (connection) |
| `TransportUnavailableException` | Network unavailable |

### HSDP Remaining

| Class | Purpose |
|-------|---------|
| `HSDPConfigurationKt` | Extension: gets hsdpIdentifier from config |
| `HSDPControlPairingHandler` | Interface: performPair/performUnpair |
| `HSDPMessageListener` | `messageReceived(ControlModel.Received)` |
| `Logger` | Wraps obfuscated logging (debug/info/error) |

---

## Appendix A: Complete File Inventory

### connectivity/condor/ (120 files) - ALL DOCUMENTED

Every file is documented in sections 3-70 and the reference tables in sections 71-79.

### cl/daconnect/ (269 files)

| Directory | Files | Purpose |
|-----------|-------|---------|
| `authentication/` | 15 | OAuth providers, token models (sections 18-19, 63-64) |
| `core/` | 33 | Configuration, models, errors, credentials |
| `device/` | 7 | Device model |
| `device_control/` | 37 | MQTT device control, remote commands |
| `device_management/` | 35 | BLE/SoftAP provisioning |
| `iot/` | 94 | IoT REST API (models, responses, params) |
| `notification/` | 10 | Push notifications |
| Root | 38 | Core SDK interfaces |

The DaConnect SDK is a thin wrapper around the Condor SDK. `DaAuthenticationService` (section 18) provides credentials, `DaIoTCredentialsProvider` (section 63) provides MQTT connection info. The `device_control/mqtt/` package uses `MqttConnectionInfo` to connect via the HSDP MQTT service. The `iot/` package is a REST API client for the Philips IoT cloud (device registration, firmware, analytics).

### connectivity/hsdpclient/ (670 files)

Auto-generated HSDP API client library. Key non-generated files:

| File/Package | Purpose |
|-------------|---------|
| `api/service/ControlServiceV1` | MQTT device control service interface |
| `api/service/IdentityAccessManagementServiceV2` | IAM token endpoints |
| `api/service/DiscoveryServiceV1` | Service discovery |
| `api/service/ProvisioningServiceV1` | Device provisioning |
| `mqtt/` | Paho MQTT client wrapper |
| `authorization/PassiveRefreshPolicy` | Token refresh using refresh_token |
| `api/model/ControlModel` | MQTT command/received models |

The `generated/` directory (480+ files) contains Swagger-generated data classes for all HSDP APIs (IAM, firmware, TDR, pairing, provisioning, blob repository, profile, control, discovery). These are standard Kotlin data classes with Gson annotations.

### ka/oneka/ (14,236 files)

The Android app UI layer. Contains no protocol implementation. Uses Condor SDK and DaConnect SDK for all device communication.

| Package | Files | Content |
|---------|-------|---------|
| `app/` | 5,565 | Activities, Fragments, ViewModels, DI, navigation |
| `domain/` | 3,556 | Use cases, repositories, domain entities |
| `backend/` | 1,752 | Backend API clients (Gigya CDC, HSDP IAM bridge) |
| `fusion/` | 684 | FUSION device bridge, onboarding |
| `ecommerce/` | 742 | In-app purchases |
| `core/` | 452 | Shared utilities |
| `connect/` | 215 | Device connection UI (pairing wizard) |
| `di/` | 142 | Dagger modules |
| `communication/` | 81 | Communication abstractions |
| `database/` | 78 | Room DB (DAOs, entities, migrations) |
| Other | 2,525 | UI, analytics, messaging, billing, etc. |

---

## Appendix B: Verification Corrections

The following corrections were made after line-by-line re-reading of all 120 condor files:

### B.1 CommunicationStrategy Interface (Complete)

**File:** `connectivity/condor/core/communication/CommunicationStrategy.java`

```java
public interface CommunicationStrategy extends Availability<CommunicationStrategy> {
    void getProperties(String portName, int productId, ResponseHandler handler);
    void putProperties(Map<String, Object> data, String portName, int productId, ResponseHandler handler);
    void addProperties(Map<String, Object> data, String portName, int productId, ResponseHandler handler);
    void deleteProperties(String portName, int productId, ResponseHandler handler);
    void subscribe(String portName, int productId, int ttl, ResponseHandler handler);
    void unsubscribe(String portName, int productId, ResponseHandler handler);
    void execMethod(String portName, int productId, String methodName, List<Object> params, ResponseHandler handler);
    int getSubscriptionTtl();
    String processByteArrayToJsonString(byte[] data);
    void addSubscriptionEventListener(SubscriptionEventListener listener);
    void removeSubscriptionEventListener(SubscriptionEventListener listener);
    void enableCommunication();
    void disableCommunication();
}
```

### B.2 CombinedCommunicationStrategy (Expanded)

The doc was too brief. Full behavior from decompiled code:

```java
public class CombinedCommunicationStrategy extends ObservableCommunicationStrategy {
    static final int TRANSPORT_SWITCH_COOL_DOWN_PERIOD_IN_MILLIS = 1000;

    private final LinkedHashSet<CommunicationStrategy> communicationStrategies; // Insertion-ordered
    private CommunicationStrategy previousStrategy;        // Last active strategy
    private final NullCommunicationStrategy nullStrategy;  // Fallback
    private final Set<Subscription> subscriptions;         // Active subscriptions
    private final Handler availabilityHandler;             // Main thread
    private final Runnable strategyChangeRunnable;         // Cool-down delayed callback

    // Inner class: tracks active subscriptions for re-subscription on transport switch
    static class Subscription {
        final String portname;
        final int productId;
        final int ttl;
        // equals/hashCode based on portname + productId (not ttl)
        void subscribe(CommunicationStrategy cs, ResponseHandler handler) { ... }
        void unsubscribe(CommunicationStrategy cs, ResponseHandler handler) { ... }
    }

    // On availability change from any sub-strategy:
    // 1. Remove any pending cool-down callback
    // 2. Schedule new callback after 1000ms (cool-down)
    // 3. After cool-down: check if best strategy changed
    // 4. If changed: unsubscribe all from old, resubscribe all on new
    // 5. Update previousStrategy

    // subscribe(): on success, adds Subscription to subscriptions set
    // unsubscribe(): removes Subscription from set, then calls unsubscribe on active strategy
    // All other ops (get/put/add/delete/exec): delegate to findStrategy()
    // addSubscriptionEventListener: registers on ALL strategies (not just active)
    // enableCommunication/disableCommunication: calls on ALL strategies
    // processByteArrayToJsonString: delegates to findStrategy()
}
```

### B.3 CondorPortProperties Interface

```java
// Marker interface - no methods. All port property classes implement this.
@Keep // Prevents ProGuard from removing
public interface CondorPortProperties {}
```

### B.4 SecurityPort

```java
public class SecurityPort extends CondorPort<SecurityPortProperties> {
    private static final String SECURITYPORT_NAME = "security";
    private static final int SECURITYPORT_PRODUCTID = 0;
    public String getCondorPortName() { return "security"; }
    public int getCondorProductId() { return 0; }
}
```

### B.5 FirmwareUpdateListener Interface (Complete)

```java
public interface FirmwareUpdateListener {
    default void onFirmwareAvailable(String version) {}  // New version available
    default void onDownloadProgress(int bytesUploaded, int totalSize) {} // Upload progress
    default void onDownloadFinished() {}                 // Upload complete
    default void onDownloadFailed(FirmwareUpdateException e) {} // Upload failed
    default void onCheckingProgress(int progress, int size) {}  // Device checking firmware
    default void onDeployFinished() {}                   // Install complete (device rebooted to IDLE)
    default void onDeployFailed(FirmwareUpdateException e) {}  // Install failed
    default void onCancelFinished() {}                   // Cancel complete (device in ERROR state)
    default void onCancelFailed() {}                     // Cancel timed out
}
```

### B.6 FirmwareUpdateOperation Interface

```java
public interface FirmwareUpdateOperation {
    void start(long timeoutMs);
    void deploy(long timeoutMs);
    void cancel(long timeoutMs);
    void finish();
}
```

### B.7 FirmwareUpdatePushLocal (Full State Machine)

```java
// State machine for local firmware upload:
//
//   start(timeout)
//     |
//     v
//   obtainApplianceState() -- get current firmware port state
//     |
//     +-- state == IDLE --> startWaitingForDownloadingState()
//     |                       |
//     |                       v
//     |                     transitionToState(DOWNLOADING) -- PUT {"state":"downloading","size":N}
//     |                       |
//     |                       v
//     |                     poll until state == DOWNLOADING (10s timeout)
//     |                       |
//     |                       v
//     |                     uploader.startAt(0) -- start chunked upload
//     |
//     +-- state == DOWNLOADING && isResuming --> uploader.startAt(progress) -- resume
//     |
//     +-- other state --> transitionToState(CANCELING) --> transitionToState(IDLE) --> retry
//
//   Chunk upload loop (FirmwareUploader):
//     1. Read maxChunkSize from device
//     2. Copy chunk: firmwareData[progress .. progress+chunkSize]
//     3. PUT {"data": <base64_chunk>} to firmware port
//     4. On OUT_OF_MEMORY: reduce chunk size by 50, retry
//     5. Check progress from cachedProperties
//     6. If progress >= totalSize: waitForReadyState()
//     7. Else: uploadChunk(progress) -- loop
//
//   waitForReadyState():
//     - Subscribe to firmware port
//     - Poll until state == READY (firmware verified by device)
//     - On READY: listener.onSuccess() -> onDownloadFinished()
//     - On ERROR: listener.onError() -> onDownloadFailed()
//
//   deploy(timeout):
//     - transitionToState(PROGRAMMING) -- PUT {"state":"go"}
//     - Poll until state == IDLE (device rebooted) -> onDeployFinished()
//     - On ERROR -> onDeployFailed()
//     - On timeout -> onDeployFailed("Timed out waiting for appliance")
//
//   cancel(timeout):
//     - Stop uploader + all pollers
//     - transitionToState(CANCELING) -- PUT {"state":"cancel"}
//     - Poll until state == ERROR (device acknowledges cancel) -> onCancelFinished()
//     - On timeout -> onCancelFailed()

// Constants:
static final long DOWNLOADING_STATE_TRANSITION_TIMEOUT_MILLIS = 10000; // 10s
static final long POLLING_INTERVAL = 1000;                              // 1s
```

### B.8 FirmwareUploader (Chunked Upload)

```java
public class FirmwareUploader {
    static final int CHUNK_SIZE_REDUCTION = 50; // Bytes to reduce on OUT_OF_MEMORY

    // Upload flow:
    // 1. Read maxChunkSize from cached FirmwarePortProperties
    // 2. If maxChunkSize <= 0: error
    // 3. Calculate effective chunk size: maxChunkSize - (reductionCount * 50)
    // 4. Copy chunk from firmwareData[offset .. offset+chunkSize]
    // 5. Create FirmwarePortProperties with data = chunk bytes
    // 6. PUT to firmware port
    // 7. On success: check progress, continue or waitForReady
    // 8. On OUT_OF_MEMORY: increment reductionCount, retry same offset
    // 9. On other error: abort

    // waitForReadyState():
    //   - If already READY: success
    //   - Else: subscribe + poll, wait for READY or ERROR
}
```

### B.9 WiFiNode

**File:** `connectivity/condor/core/port/common/WiFiNode.java`

Missing from original doc. Represents a WiFi network in scan results.

```java
public class WiFiNode {
    @SerializedName("channel")  String channel;   // WiFi channel
    @SerializedName("quality")  Integer quality;   // Signal quality
    @SerializedName("security") String security;   // "WPA2", "OPEN", etc.
    @SerializedName("ssid")     String ssid;       // Network name
}
```

### B.10 WifiNetworkPortResponseDeserializer

**File:** `connectivity/condor/core/port/common/WifiNetworkPortResponseDeserializer.java`

Custom Gson deserializer that handles the WiFi networks response format.

```java
// The device returns WiFi networks as a JSON object where each key is the SSID
// and the value is the network details. This deserializer converts it to a list.
// Example response: {"MyNetwork": {"channel":"6","quality":80,"security":"WPA2"}, ...}
// Deserialized to: WifiNetworksPortProperties with List<WiFiNode>
```

### B.11 IntegerPreservingMapDeserializer (Detailed)

Recursively deserializes JSON preserving integers (Gson defaults to Double for all numbers).

```java
// readElement() dispatches on JSON type:
//   - JsonArray: recursively read each element, return ArrayList
//   - JsonObject: recursively read each value, return LinkedHashMap<String, Object>
//   - JsonPrimitive: readPrimitive()
//
// readPrimitive():
//   - Boolean -> Boolean
//   - String -> String
//   - Number: if toString() contains '.': Double, else: Integer
//     (This preserves {"value": 42} as Integer(42) instead of Double(42.0))
```

### B.12 VerboseExecutor (Detailed)

Not just a debug wrapper. Tracks executing task count for idle detection.

```java
public class VerboseExecutor extends ThreadPoolExecutor {
    // Single thread (corePoolSize=1, maxPoolSize=1)
    // Increments counter in beforeExecute(), decrements in afterExecute()
    // isIdle(): true when counter==0 AND queue is empty
    // Used by LanCommunicationStrategy to know when request queue is drained
}
```

### B.13 DatabaseFetcher

```java
public interface DatabaseFetcher {
    NetworkNodeDatabase getNetworkNodeDatabase(RuntimeConfiguration config);
}
// Note: DatabaseFetcher is in core/ root, not core/store/
// NetworkNodeDatabaseFactory is in core/store/ and implements the actual creation
```

### B.14 Availability Interface

```java
public interface Availability<T> {
    boolean isAvailable();
    void addAvailabilityListener(AvailabilityListener<T> listener);
    void removeAvailabilityListener(AvailabilityListener<T> listener);

    interface AvailabilityListener<T> {
        void onAvailabilityChanged(T source);
    }
}
```

### B.15 RemoteRequestType Enum

```java
public enum RemoteRequestType {
    ADD_PROPS,       // POST equivalent
    DEL_PROPS,       // DELETE equivalent
    EXEC_METHOD,     // RPC method invocation
    GET_PORTS,       // List available ports
    GET_PRODS,       // List available products
    GET_PROPS,       // GET equivalent
    PUT_PROPS,       // PUT equivalent
    SUBSCRIBE,       // Subscribe for events
    UNSUBSCRIBE      // Unsubscribe
}
```

### B.16 SubscribeRequest

**File:** `connectivity/condor/lan/communication/SubscribeRequest.java`

LAN subscription request adds the UDP port to the subscription payload.

```java
public class SubscribeRequest extends LanRequest {
    private final int udpPort; // The local UDP port for push events

    // Overrides getSubscriptionData() to add "changeudp" key:
    // {"subscriber": "<appId>", "ttl": 300, "changeudp": <udpPort>}

    @Override
    public Response handleHttpOk(Headers headers, String body) {
        // Check for "X-Condor-Features: changeindication-port" header
        // This confirms the device supports UDP push events
        String features = headers.get("X-Condor-Features");
        if (features != null && features.contains("changeindication-port")) {
            // Device supports UDP change indication
        }
        return super.handleHttpOk(headers, body);
    }
}
```

### B.17 FasterFirmwareUploadRequest

**File:** `connectivity/condor/lan/communication/FasterFirmwareUploadRequest.java`

Firmware data upload via HTTP PUT to `https://{ip}/firmwaredata`.

```java
public class FasterFirmwareUploadRequest extends LanRequest {
    // URL: https://{ip}/firmwaredata (not /di/v1/products/...)
    // Method: PUT with binary body (application/octet-stream)
    // Used for faster bulk firmware transfer (bypasses JSON encoding)
}
```

### B.18 IOUtil

```java
public class IOUtil {
    public static long copyStream(InputStream in, OutputStream out) throws IOException {
        byte[] buffer = new byte[65536]; // 64KB buffer
        long total = 0;
        int read;
        while ((read = in.read(buffer)) != -1) {
            out.write(buffer, 0, read);
            total += read;
        }
        return total;
    }
}
```

### B.19 IPProvider

```java
public class IPProvider {
    public String getLocalIpAddress(Context context) {
        WifiManager wm = (WifiManager) context.getApplicationContext().getSystemService("wifi");
        int ip = wm.getConnectionInfo().getIpAddress();
        // Convert int to dotted-decimal: ip & 0xff, (ip >> 8) & 0xff, ...
        return String.format(Locale.US, "%d.%d.%d.%d",
            ip & 0xff, (ip >> 8) & 0xff, (ip >> 16) & 0xff, (ip >> 24) & 0xff);
    }
}
```

### B.20 MulticastLockControlPoint

```java
public class MulticastLockControlPoint {
    private WifiManager.MulticastLock multicastLock;

    public boolean acquireMulticastLock() {
        if (multicastLock == null) {
            WifiManager wm = (WifiManager) context.getSystemService("wifi");
            multicastLock = wm.createMulticastLock("CondorMulticastLock");
            multicastLock.setReferenceCounted(true);
        }
        if (!multicastLock.isHeld()) {
            multicastLock.acquire();
        }
        return multicastLock.isHeld();
    }

    public void releaseMulticastLock() {
        if (multicastLock != null && multicastLock.isHeld()) {
            multicastLock.release();
        }
    }
}
```

---

## Appendix C: End-to-End Flows

These flow diagrams show how the documented classes chain together to implement complete operations. Every arrow references a method from the class documentation above.

### C.1 Device Lifecycle (Discovery to Polling)

```
1. STARTUP
   CondorEntryPoint(applianceFactory, runtimeConfig, lanTransportContext, hsdpTransportContext)
     |
     +-> Creates ApplianceManager with all DiscoveryStrategies
     +-> Loads all NetworkNodes from SQLite (NetworkNodeDatabase.getAll())
     +-> For each loaded node: ApplianceFactory.createApplianceForNode()
     |     Creates Appliance with CombinedCommunicationStrategy(lanStrategy, hsdpStrategy)
     +-> Registers PropertyChangeListener on each node (auto-saves to DB on change)

2. DISCOVERY
   CondorEntryPoint.startDiscovery()
     |
     +-> BaseLanDiscoveryStrategy.start()
           +-> MulticastLockControlPoint.acquireMulticastLock()
           +-> ssdpControlPoint.start()   // SSDP multicast
           +-> mMDNSControlPoint.start()  // mDNS browse
           |
           |  [Device found via SSDP or mDNS]
           |
           +-> BaseLanDiscoveryStrategy.onDeviceDiscovered(DiscoveredLanDevice)
                 +-> createNetworkNode() // cppId, IP, name, model, SSID, bootId
                 +-> nodePassesFilter()  // check modelId filter
                 +-> DeviceCache.add(node, expirationCallback, ttlMillis)
                 +-> ObservableDiscoveryStrategy.notifyNetworkNodeDiscovered(node)
                       |
                       +-> [on main thread]
                           ApplianceManager.onNetworkNodeDiscovered(node)
                             +-> processDiscoveredOrLoadedNetworkNode(node)
                                   |
                                   +-> Already discovered? -> updateAppliance() -> notify updated
                                   +-> Known from DB? -> move to discovered map -> notify found
                                   +-> New? -> ApplianceFactory.createApplianceForNode(node)
                                         +-> knownAppliances.put(cppId, appliance)
                                         +-> discoveredAppliances.put(cppId, appliance)
                                         +-> notify ApplianceListener.onApplianceFound()

3. CACHE EXPIRATION
   DeviceCache timer fires after TTL (default 15s if no re-discovery)
     +-> ExpirationCallback.onCacheExpired(node)
           +-> DeviceCache.remove(cppId)
           +-> If not persisted in DB: remove from discoveredAppliances
           +-> ApplianceListener.onApplianceLost(appliance)
```

### C.2 LAN Request Execution (Single GET)

```
App calls: condorPort.getProperties(callback)
  |
  v
CondorPort.tryToPerformNextRequest()        [synchronized]
  +-> isRequestInProgress? return           [serialize requests]
  +-> Priority: put > subscribe > unsubscribe > get > execMethod
  +-> isRequestInProgress = true
  +-> performGetProperties()
        |
        v
      communicationStrategy.getProperties("air", 1, responseHandler)
        |
        v [CombinedCommunicationStrategy]
      findStrategy()
        +-> iterate LinkedHashSet in insertion order (LAN first, HSDP second)
        +-> return first where isAvailable() == true
        +-> if none: return NullCommunicationStrategy (all ops return NOT_CONNECTED)
        |
        v [LanCommunicationStrategy]
      exchangeKeyIfNecessary(networkNode)
        +-> if encryptionKey == null && !isKeyExchangeOngoing:
              doKeyExchange() -> GetKeyRequest to /di/v1/products/0/security
              -> addRequestInFrontOfQueue (priority)
      createUnauthorizedHandlingRequest("air", 1, GET, null, handler)
        +-> wraps handler to auto-retry on REQUEST_UNAUTHORIZED
      requestQueue.addRequest(lanRequest)
        |
        v [RequestQueue - background HandlerThread]
      LanRequest.execute()
        |
        +-> createURL: "https://{ip}/di/v1/products/1/air"
        +-> createRequestBuilder: URL + "Connection: keep-alive"
        +-> Add "Authorization" header if credentials cached
        +-> For GET: no body. For PUT/POST/DELETE: JSON body
        +-> lanTransportContext.createOrGetOkHttpClient(networkNode)
        |     +-> Check cache by cppId
        |     +-> If miss: createOkHttpClient()
        |           +-> createLANOnlyNetwork() (wait 3s for WiFi/Ethernet)
        |           +-> TLSv1.2 + SslPinTrustManager (TOFU cert pinning)
        |           +-> hostnameVerifier: accept all (IP addresses)
        |           +-> cipher suites: ECDHE_RSA_AES128_GCM, ECDHE_ECDSA_AES128_GCM, RSA_AES128_CBC_SHA256
        +-> resetClientTimeout(30ms) [see note in B.7]
        +-> client.newCall(request).execute()  [BLOCKING]
        |
        v [HTTP Response]
      switch(statusCode):
        200 -> handleHttpOk(headers, body)
               +-> if empty: return EMPTY_RESPONSE error
               +-> return Response(body, null)  // SUCCESS
        400 -> return Response(body, NOT_UNDERSTOOD)
        401 -> handleUnauthorized(headers, body)
               +-> networkNode.setCredentials(null)  // clear cache
               +-> challenge = headers.get("WWW-Authenticate")
               +-> newCreds = createCredentialsFrom(challenge)  // see C.3
               +-> networkNode.setCredentials(newCreds)
               +-> return Response(body, REQUEST_UNAUTHORIZED)
               +-> [createUnauthorizedHandlingRequest retries with new creds]
        429 -> return Response(null, BUSY)
        502 -> return Response(null, CANNOT_CONNECT)
        other -> findErrorInResponseBody(body) or REQUEST_FAILED
        SSLHandshakeException -> INSECURE_CONNECTION
        IOException -> IOEXCEPTION
        |
        v [Back on main thread via responseHandler.post()]
      Request.notifyResponseHandler(response)
        +-> if error: mResponseHandler.onError(error, data)
        +-> if success: mResponseHandler.onSuccess(data.getBytes(UTF_8))
              |
              v [Back in CondorPort]
            processResponse(bytes)
              +-> communicationStrategy.processByteArrayToJsonString(bytes)
              |     [LanCommunicationStrategy: just UTF-8 decode]
              |     [For encrypted devices: Crypto.decryptData() - see C.4]
              +-> propertiesFromJsonString(json)
              |     +-> parse incoming JSON
              |     +-> MERGE with cached properties (not replace)
              |     +-> deserialize merged JSON to P (CondorPortProperties subclass)
              +-> setPortProperties(merged)  // update cache
            flushGetPropertiesCallbacks(SuccessResult(cachedProperties))
            requestCompleted()
              +-> isRequestInProgress = false
              +-> tryToPerformNextRequest()  // process next queued request
```

### C.3 PhilipsCondor Challenge-Response

```
Server sends:  HTTP 401
               WWW-Authenticate: PHILIPS-Condor <base64_challenge>

Client computes (LanRequest.createCredentialsFrom):

  1. raw_challenge = base64_decode(strip_scheme_prefix(header_value))
     // Regex: (?i)PHILIPS-Condor  (case-insensitive strip)

  2. Validate: raw_challenge.length == 16 bytes

  3. Validate: clientId and clientSecret both non-null and non-empty

  4. clientId_bytes  = base64_decode(networkNode.getClientId())
     clientSecret_bytes = base64_decode(networkNode.getClientSecret())

  5. to_hash = concatenate(raw_challenge, clientId_bytes, clientSecret_bytes)
     // = 16 + 16 + 16 = 48 bytes

  6. hash = SHA-256(to_hash)
     // = 32 bytes

  7. response_bytes = concatenate(clientId_bytes, hash)
     // = 16 + 32 = 48 bytes

  8. header_value = "PHILIPS-Condor " + base64_encode(response_bytes)

Client retries: same request with Authorization: <header_value>
Server responds: HTTP 200 (if credentials valid)

Credentials are cached in networkNode.credentials for subsequent requests.
Cache is cleared on next 401.
```

### C.4 AES Decryption Pipeline (Encrypted Devices)

```
For devices where https=0 in the APK database (HTTP devices with payload encryption):

LanCommunicationStrategy overrides processByteArrayToJsonString():
  -> delegates to Crypto.decryptData(utf8String)

Crypto.decryptData(data):
  |
  +-> if data.isEmpty(): return null
  +-> encryptionKey = networkNode.getEncryptionKey()
  +-> if encryptionKey is null/empty:
  |     notifyDecryptionFailedListener()  // triggers key re-exchange
  |     return null
  |
  +-> Step 1: Strip whitespace (char-by-char, compareTo(' ') <= 0)
  +-> Step 2: base64_decode(trimmed_data) -> byte[] ciphertext
  +-> Step 3: AES decrypt
  |     cipher = Cipher.getInstance("AES/CBC/PKCS7Padding")
  |     key_bytes = BigInteger(encryptionKey, 16).toByteArray()
  |     if key_bytes[0] == 0: key = key_bytes[1..17]  // strip sign byte
  |     else: key = key_bytes[0..16]
  |     iv = 16 zero bytes
  |     cipher.init(DECRYPT_MODE, SecretKeySpec(key, "AES"), IvParameterSpec(iv))
  |     plaintext_bytes = cipher.doFinal(ciphertext)
  +-> Step 4: removeRandomBytes(plaintext_bytes)
  |     strip first 2 bytes (random nonce prepended before encryption)
  +-> Step 5: new String(stripped_bytes, defaultCharset) -> JSON string
  +-> Step 6: Validate JSON: GsonProvider.get().fromJson(json, Map.class)
  |     if JsonSyntaxException: return null
  +-> return json
  |
  +-> On any GeneralSecurityException or IllegalArgumentException:
        notifyDecryptionFailedListener()
          -> LanCommunicationStrategy.triggerKeyExchange(networkNode)
               -> networkNode.setEncryptionKey(null)
               -> exchangeKeyIfNecessary(networkNode)
                    -> GetKeyRequest to GET /di/v1/products/0/security
                    -> response: {"key": "abcdef...", "nextkey": null}
                    -> networkNode.setEncryptionKey(hexKey)
```

### C.5 LAN Subscription Lifecycle

```
1. SUBSCRIBE
   CondorPort.subscribe(callback)
     +-> communicationStrategy.addSubscriptionEventListener(subscriptionEventListener)
     +-> isSubscribed = true
     +-> Schedule resubscriptionRunnable after TTL seconds
     +-> tryToPerformNextRequest() -> performSubscribe()
           |
           v [LanCommunicationStrategy]
         localSubscriptionHandler.enableSubscription(networkNode, listeners)
           +-> UdpEventReceiver.getInstance().startReceivingEvents(this)
           |     +-> Create UdpReceivingThread if not running
           |     +-> Bind DatagramSocket to port 8080 (or random port on failure)
           |     +-> socketSetupLatch.countDown() // signal ready
           |     +-> Return bound port number
           +-> boundSubscriptionUdpPort = returnedPort
         exchangeKeyIfNecessary(networkNode)
         createSubscribeRequest(portName, productId, ttl, udpPort, handler)
           +-> POST /di/v1/products/{id}/{port}
           +-> Body: {"subscriber": "deadbeef{random}", "ttl": 300, "changeudp": {udpPort}}
           +-> On success: check X-Condor-Features header contains "changeindication-port"
           +-> If missing: return NOT_SUBSCRIBED error

2. RECEIVE EVENT
   UdpReceivingThread.receiveDatagram()
     +-> socket.receive(packet)  // BLOCKING, 1024 byte buffer
     +-> Parse packet:
     |     Line 0: "NOTIFY /di/v1/products/{id}/{port} HTTP/1.1"
     |     Lines 1..N-1: HTTP-style headers
     |     Line N (last): encrypted payload (base64)
     +-> Extract: portName from line 0 path, senderIP from packet address
     +-> UdpEventListener.onUDPEventReceived(payload, portName, senderIP)
           |
           v [LocalSubscriptionHandler]
         +-> Check senderIP matches networkNode.getIpAddress()
         +-> Crypto.decryptData(payload) // same AES pipeline as HTTP responses
         +-> if null: postSubscriptionEventDecryptionFailureOnUiThread()
         |     -> CondorPort.subscriptionEventListener.onSubscriptionEventDecryptionFailed()
         |          -> fallback: getProperties() to reload full state
         +-> else: postSubscriptionEventOnUiThread(portName, decryptedBytes, listeners)
               -> CondorPort.subscriptionEventListener.onSubscriptionEventReceived()
                    +-> pathMatchesMyPort(portName)?
                    +-> processResponse(bytes) // MERGE into cached properties
                    +-> notify all PortSubscriptionListeners.onPortSubscriptionEvent()

3. AUTO-RESUBSCRIBE
   After TTL seconds, resubscriptionRunnable fires:
     +-> refreshSubscriptionIfNecessary()
           +-> if isSubscribed: subscribe(callback) // re-sends POST with same TTL
           +-> Reschedule resubscriptionRunnable for another TTL seconds
   Also triggers on NetworkNode bootId change (device rebooted):
     +-> networkNodeListener detects KEY_BOOT_ID PropertyChangeEvent
     +-> refreshSubscriptionIfNecessary()

4. UNSUBSCRIBE
   CondorPort.unsubscribe(callback)
     +-> communicationStrategy.removeSubscriptionEventListener(listener)
     +-> stopResubscribe(): isSubscribed=false, cancel resubscriptionRunnable
     +-> performUnsubscribe()
           +-> DELETE /di/v1/products/{id}/{port}
           +-> Body: {"subscriber": "deadbeef{random}"}
     +-> localSubscriptionHandler.disableSubscription()
           +-> UdpEventReceiver.stopReceivingEvents(listener)
                 +-> Remove listener from set
                 +-> If no listeners left: udpReceivingThread.stopThread()
                       +-> close socket, release multicast lock
```

### C.6 Transport Switching (LAN <-> HSDP)

```
CombinedCommunicationStrategy monitors all sub-strategies via AvailabilityListeners.

When ANY sub-strategy availability changes:
  |
  +-> lambda$new$0(strategy)
        +-> Remove pending strategyChangeRunnable from availabilityHandler
        +-> Post new strategyChangeRunnable with 1000ms delay (cool-down)
              |
              +-> [After 1000ms cool-down]
                  lambda$new$1()
                    +-> newStrategy = firstAvailableStrategy()
                    |     iterate LinkedHashSet: return first isAvailable()
                    +-> if newStrategy == previousStrategy: return (no change)
                    +-> if either is null: notifyAvailabilityChanged()
                    |
                    +-> For each active Subscription in subscriptions set:
                    |     subscription.unsubscribe(previousStrategy, logHandler)
                    |     subscription.subscribe(newStrategy, logHandler)
                    |
                    +-> previousStrategy = newStrategy

LAN availability (LanCommunicationStrategy.isAvailable):
  networkNode.ipAddress != null
  AND connectivityMonitor.isAvailable() (WiFi/Ethernet connected)
  AND isOnSameNetwork() (current SSID matches stored SSID, or SSID unknown)

HSDP availability (HSDPCommunicationStrategy.isAvailable):
  connectivityMonitor.isAvailable() (internet connected)
  AND networkNode.hsdpId != null
```

### C.7 HSDP Authentication State Machine

```
HSDPController.connect(completable)
  |
  +-> authentication.signOn(callback)
        |
        +-> Has cached tokenSet?
        |     YES: applyRefreshPolicy(tokens) -> discoverServices() -> DONE
        |     NO:
        |       +-> isProvisioningRequired() (no provisionedIdentity)?
        |             YES: Full bootstrap flow:
        |               1. bootstrapSignOn(callback)
        |                    iamService.getAccessToken(bootstrapClientId, bootstrapClientSecret)
        |               2. discoverServices(callback)
        |                    discoveryService.getServices() -> store service URLs
        |               3. provision(callback)
        |                    Find "PRV" service URL from discovered services
        |                    provisioningService.createIdentity(evidence)
        |                    -> returns: clientId, clientSecret, loginId, password, hsdpId, signature
        |                    -> persistIdentity(HSDPIdentity)
        |               4. signOn(callback) // RECURSIVE: now has identity
        |
        |             NO: provisionedSignOn(callback)
        |               +-> identity = configuration.getProvisionedIdentity()
        |               +-> Check cached token expiry (120s threshold)
        |               +-> If valid: use cached -> DONE
        |               +-> If expired:
        |                     iamService.getAccessToken(identity.clientId, identity.clientSecret,
        |                                               identity.username, identity.password)
        |                     -> store provisionedTokenResponse
        |                     -> compute expiry: expiresIn + (currentTime / 1000)
        |                     -> persistAccessTokens(tokenResponse)
        |                     -> discoverServices(callback)
        |                     -> restartExpiryTimer(expiresIn)
        |
        v [signOn complete]
      lambda$connect$5(completable, authError)
        +-> if authError: completable.onCompleted(AUTHENTICATION_FAILED)
        +-> createControlService()
        |     +-> authentication.findServiceForTag("IOT")
        |     +-> Find URL starting with "wss://"
        |     +-> Parse: "wss://host/path?topic-prefix=prefix"
        |     +-> serviceFactory.createControlServiceV1(hsdpId, wssUrl, topicPrefix)
        +-> if null: completable.onCompleted(NO_CONTROL_SERVICE_AVAILABLE)
        +-> if accessToken null: completable.onCompleted(MISSING_ACCESS_TOKEN)
        +-> if signedToken null: completable.onCompleted(MISSING_SIGNED_TOKEN)
        +-> controlServiceV1.addListener(controlServiceListener)
        +-> controlServiceV1.connect(accessToken, signedToken, callback)
              +-> on error: completable.onCompleted(CONNECT_FAILED)
              +-> on success: completable.onCompleted(null)  // CONNECTED

TOKEN REFRESH (automatic):
  restartExpiryTimer(expiresInSeconds):
    +-> if expiresIn - 30 < 0: report TOKEN_EXPIRY_TOO_SHORT
    +-> Schedule timer at (expiresIn * 1000) ms
    +-> On fire:
          if tokenSet available: configuration.refreshAccessTokens()
          else: provisionedSignOn()
    +-> AuthenticationListener.onAccessTokensRefreshed()
          -> HSDPController disconnects + reconnects with new tokens
```

### C.8 HSDP Command Execution (Remote Request)

```
App calls: condorPort.getProperties(callback)
  -> CombinedCommunicationStrategy selects HSDP (LAN unavailable)
  -> HSDPCommunicationStrategy.getProperties("air", 1, handler)
       |
       v
     requestQueue.addRequest(new HSDPRemoteRequest(
       operation=GET_PROPS, hsdpId, productId=1, portName="air",
       data=null, handler, messenger))
       |
       v [RequestQueue background thread]
     HSDPRemoteRequest.execute()
       |
       +-> identifier = UUID.randomUUID()
       +-> sendCommandLatch = CountDownLatch(1)
       +-> responseLatch = CountDownLatch(1)
       +-> messenger.registerMessageListener(this)
       |
       +-> Create command detail JSON:
       |     {
       |       "condorVersion": "1",
       |       "op": "GetProps",
       |       "path": "1/air",
       |       "values": null
       |     }
       |
       +-> messenger.sendCommand(hsdpId, command, sendCommandCompletion)
       |     |
       |     v [HSDPCommandQueue]
       |   queue.submit(() -> {
       |     semaphore.acquire()  // Wait for previous command to finish
       |     hsdpMessenger.sendCommand(...)
       |       |
       |       v [HSDPController]
       |     if connected: controlServiceV1.sendCommand(hsdpId, command, callback)
       |     else: connect() first, then sendCommand()
       |       -> callback: semaphore.release()
       |   })
       |
       +-> sendCommandLatch.await(30000ms)  // Wait for MQTT publish ACK
       |     On timeout: return SEND_FAILED
       |     sendCommandCompletion fires: sendCommandLatch.countDown()
       |
       +-> responseLatch.await(30000ms)  // Wait for device response
       |
       |   [Meanwhile, MQTT messages arrive via controlServiceListener]:
       |
       |   ControlServiceV1.Listener.onCommandReceived(received)
       |     -> HSDPController.notifyMessageListeners(received)
       |       -> HSDPRemoteRequest.messageReceived(received)
       |            |
       |            +-> type == "accepted": ignore (just an ACK)
       |            +-> type == "rejected":
       |            |     commandRejected = true
       |            |     responseLatch.countDown()
       |            +-> type == "notification":
       |                  if command.cmdName == this.identifier:  // UUID match
       |                    responseString = command.statusDetailAsJsonString
       |                    responseLatch.countDown()
       |
       +-> [responseLatch released]
       |
       +-> messenger.unregisterMessageListener(this)
       +-> if commandRejected: return REJECTED
       +-> if responseString null: return REQUEST_FAILED
       +-> Parse Condor response:
       |     status = extractStatus(responseString)  // CondorControlMessage.status
       |     if status != 0: return Error.getErrorForCode(status)
       |     data = extractData(responseString)      // JSON-serialize .values field
       +-> return Response(data, null)  // SUCCESS
```

### C.9 Firmware Update State Machine

```
FirmwareUpdatePushLocal.start(timeoutMs)
  |
  +-> Set watchdog timer (timeoutMs) -> completeFirmwareUpdate(false, "Timed out")
  +-> Create FirmwarePortProperties with size = firmwareData.length
  +-> obtainApplianceState() -> getProperties from firmware port
        |
        v
      Check current state:
        |
        +-> state == IDLE:
        |     startWaitingForDownloadingState()
        |
        +-> state == DOWNLOADING && isResuming && size matches:
        |     uploader.startAt(progress)  // Resume interrupted upload
        |
        +-> other state (e.g., ERROR, CHECKING):
              transitionToState(CANCELING)
                PUT {"state": "cancel"} to firmware port
              -> transitionToState(IDLE)
                   PUT {"state": "idle"} to firmware port
              -> startWaitingForDownloadingState()

startWaitingForDownloadingState():
  +-> transitionToState(DOWNLOADING)
  |     PUT {"state": "downloading", "size": N} to firmware port
  +-> Start downloadingStatePoller (1s interval, 10s timeout)
  |     Poll firmware port every 1s:
  |       state == DOWNLOADING: stop poller, uploader.startAt(0)
  |       state == ERROR: stop poller, fail
  |       timeout: fail("Timed out while waiting for downloading state")

FirmwareUploader.startAt(offset):
  +-> Read maxChunkSize from cached FirmwarePortProperties
  +-> chunkSizeReductionCount = 0
  +-> uploadChunk(offset):
        |
        +-> effectiveChunkSize = maxChunkSize - (reductionCount * 50)
        +-> chunk = firmwareData[offset .. offset + effectiveChunkSize]
        +-> PUT {"data": base64(chunk)} to firmware port
        +-> On success:
        |     progress = cachedProperties.getProgress()
        |     listener.onProgress(progress, totalSize)
        |     if progress >= totalSize: waitForReadyState()
        |     elif state == ERROR: fail
        |     elif state != DOWNLOADING: fail("No longer in Downloading state")
        |     else: uploadChunk(progress)  // LOOP: next chunk
        +-> On OUT_OF_MEMORY error:
        |     chunkSizeReductionCount++  // reduce chunk by 50 bytes
        |     uploadChunk(offset)        // retry same offset with smaller chunk
        +-> On other error: fail

waitForReadyState():
  +-> if already READY: success
  +-> else: subscribe to firmware port + poll
  |     On state == READY: listener.onSuccess() -> onDownloadFinished()
  |     On state == ERROR: fail(statusMessage)

FirmwareUpdatePushLocal.deploy(timeoutMs):
  +-> transitionToState(PROGRAMMING)
  |     PUT {"state": "go"} to firmware port
  +-> Start deployPoller (1s interval, timeoutMs timeout)
        On state == IDLE: stop poller -> onDeployFinished()  // Device rebooted
        On state == ERROR: stop poller -> onDeployFailed()
        On timeout: onDeployFailed("Timed out waiting for appliance")

FirmwareUpdatePushLocal.cancel(timeoutMs):
  +-> Stop uploader + all pollers
  +-> transitionToState(CANCELING)
  |     PUT {"state": "cancel"} to firmware port
  +-> Start errorOnCancelStatePoller (1s interval, timeoutMs timeout)
        On state == ERROR: stop poller -> onCancelFinished()
        On timeout: onCancelFailed()

COMPLETE STATE DIAGRAM:
  IDLE -> PUT "downloading" -> DOWNLOADING -> [upload chunks] -> READY
  READY -> PUT "go" -> PROGRAMMING -> [device reboots] -> IDLE
  Any state -> PUT "cancel" -> CANCELING -> ERROR
  CANCELING -> PUT "idle" -> IDLE (for retry)
```

### C.10 Cloud Pairing Flow (HSDP Control)

```
HSDPControlPairingHandlerImpl.performPair(callback)
  |
  +-> hsdpPairingHandler.performPairingFlow("control", hsdpIdentifier, innerCallback)
        |
        v [HsdpPairingHandler.performPairingFlowWithType(PAIR, ...)]
      1. Register HsdpPairingPortListener on pairing port
      2. port.subscribe(subscribeCallback)  // Subscribe for ChangeIndications
           |
           v [On subscribe success]
         3. isSubscribeRequested = false
         4. port.pair(type="control", trustee=hsdpIdentifier, pairingCallback)
              |
              v [HsdpPairingPort.pair()]
            execMethod("Pair", ["control", hsdpIdentifier], callback)
              |
              v [CondorPort.execMethod -> CommunicationStrategy.execMethod]
            For HSDP: creates HSDPRemoteRequest with operation=EXEC_METHOD
              Command: {"condorVersion":"1", "op":"ExecMethod", "path":"0/pairing",
                        "values": {"Pair": ["control", "hsdpIdentifier"]}}
              |
              v [Device processes pairing request]
            Response type "notification":
              result.getValue().get(0) == 0.0: pairingCallback.onPairingResult(0) // accepted
              result.getValue().get(0) != 0.0: pairingCallback.onPairingResult(-1) // rejected
              |
              v [pairingResult == 0: RPC accepted]
            5. Start 30s watchdog timer -> completePairingFlow(TIMED_OUT)
            6. Wait for ChangeIndication on pairing port...
              |
              v [ChangeIndication arrives via MQTT]
            HSDPRemoteSubscriptionHandler.messageReceived(received)
              +-> type == "notification"
              +-> operation == "ChangeIndication"
              +-> Extract path and values from CondorControlMessage
              +-> postSubscriptionEventOnUiThread(path, values, listeners)
                    |
                    v [CondorPort.subscriptionEventListener.onSubscriptionEventReceived]
                  processResponse(bytes) // merge into cachedProperties
                  HsdpPairingPortListener.onPortSubscriptionEvent(port)
                    +-> cachedProperties = port.getCachedProperties()
                    +-> Check: previousType == "control" && previousTrustee == hsdpIdentifier
                    +-> Check: previousResult == true
                    +-> completePairingFlow(callback, null)  // SUCCESS
                    |   OR
                    +-> completePairingFlow(callback, REJECTED)  // FAILED

      7. completePairingFlow():
           +-> port.unsubscribe()
           +-> port.removeSubscriptionListener(portListener)
           +-> Cancel watchdog timer
           +-> innerCallback.onPairingFlowCompleted(error)
                 |
                 v [Back in HSDPControlPairingHandlerImpl]
               if error == null:
                 completePairingFlow()
                   +-> pairingPort.getProperties()  // Read hsdpId from device
                   +-> hsdpId = properties.getHsdpId()
                   +-> networkNode.setHsdpId(hsdpId)  // Store for future HSDP communication
                   +-> callback.onPairingFlowCompleted(null)  // DONE
               else:
                 callback.onPairingFlowCompleted(error)

UNPAIR follows same flow but:
  - Uses ExecMethod("Unpair", [...]) instead of "Pair"
  - On success: networkNode.setHsdpId(null)  // Remove HSDP association
```

### C.11 LAN Pairing Flow (Local Authentication)

```
LanTransportContext.authenticate(appliance, evidence, callback)
  |
  +-> authentication.authenticate(networkNode, evidence, callback)
        |
        v [Background HandlerThread]
      authenticate$lambda$2(networkNode, this, evidence, callback)
        |
        +-> Check WiFi available via connectivityMonitor
        +-> Generate or get clientId:
        |     initNetworkNodeClientID(networkNode)
        |       if clientId null/empty: clientId = ByteUtil.create128bitBase64EncodedKey()
        |       networkNode.setClientId(clientId)
        |
        +-> Build request body:
        |     {"id": "<base64_clientId>"}
        |     If evidence map provided: merge all evidence keys into body
        |     (e.g., {"id": "...", "key": "<SHA256_evidence>"} for seed challenge)
        |
        +-> PUT https://{ip}/auth/v{version}/
        |     Content-Type: application/json
        |     OkHttp client from lanTransportContext (TLS, cert pinning)
        |
        v [Response]
      switch(statusCode):
        200:
          Parse JSON response as HashMap
          |
          +-> Extract "authenticated" boolean (default false if missing)
          +-> Extract "secret" string
          +-> Remove both from response map
          +-> If authenticated && secret != null:
          |     networkNode.setClientSecret(secret)  // STORE PAIRING SECRET
          +-> callback.response(authenticated, remainingFields, null)
          |
          |   remainingFields may contain "seed" for challenge flow:
          |   CALLER checks: if !authenticated && "seed" in remainingFields:
          |     evidence = SHA256(seed + clientId)
          |     RETRY: authenticate(networkNode, {"key": evidence}, callback)
          |
        400: callback.response(false, null, AuthenticationError("error_bad_request"))
        502: callback.response(false, null, AuthenticationError("error_bad_gateway"))
        SocketTimeout: callback.response(false, null, AuthenticationError("error_timeout"))
        SSLHandshakeException: callback.response(false, null, AuthenticationError(message))
        IOException: callback.response(false, null, AuthenticationError(message))
        TransportUnavailable: callback.response(false, null, AuthenticationError("no wifi"))

PAIRING PATTERNS:
  Pattern 1 - New device (never paired):
    PUT {"id": "abc123..."} -> {"authenticated": true, "secret": "xyz789..."}
    -> Store secret, done.

  Pattern 2 - Already-paired device (seed challenge):
    PUT {"id": "abc123..."} -> {"authenticated": false, "seed": "qrs456..."}
    Compute: evidence = SHA256(seed + clientId)
    PUT {"id": "abc123...", "key": "<evidence>"} -> {"authenticated": true, "secret": "xyz789..."}
    -> Store new secret, done.

  Pattern 3 - Already-paired, no existing secret:
    PUT {"id": "abc123..."} -> {"authenticated": false, "seed": "qrs456..."}
    Cannot compute evidence without clientSecret -> PAIRING FAILS
    User must factory reset device or use Android app to re-pair.
```

### C.12a ExecMethod Wire Format

**LAN ExecMethod** (via LanCommunicationStrategy.execMethod):
```
PUT https://{ip}/di/v1/products/{productId}/{portName}
Authorization: PHILIPS-Condor <credentials>
Content-Type: application/json

{"Pair": ["control", "hsdpIdentifier123"]}
```
The method name is the JSON key, the params list is the value.

**Response:**
```json
{"return": [0.0]}
```
The key is always `"return"`. The value is a list of return values. CondorPort.ExecMethodInfo parses this with `EXEC_METHOD_RESPONSE_KEY = "return"`.

**HSDP ExecMethod** (via HSDPRemoteRequest):
```json
{
  "condorVersion": "1",
  "op": "ExecMethod",
  "path": "0/pairing",
  "values": {"Pair": ["control", "hsdpIdentifier123"]}
}
```
Response comes as a `"notification"` MQTT message with the same `{"return": [...]}` structure inside `statusDetail.values`.

### C.12b Subscription Event Path Matching

When a subscription event arrives (UDP or MQTT), CondorPort checks if it's for this port:

```java
// pathMatchesMyPort(incomingPath):
//   Match 1: incomingPath == getCondorPortName()
//            e.g., "airfryer" == "airfryer"
//   Match 2: incomingPath == getCondorProductId() + "/" + getCondorPortName()
//            e.g., "1/airfryer" == "1/airfryer"
//   Either match: process the event
```

This dual matching is necessary because UDP events send just the port name in the NOTIFY path, while MQTT ChangeIndication events use `productId/portName` format.

### C.12c CondorPort Gson Configuration

Each CondorPort creates a custom Gson instance:

```java
this.gson = GsonProvider.get()       // base: serializeNulls + disableHtmlEscaping
    .newBuilder()
    .registerTypeHierarchyAdapter(byte[].class, new Base64Adapter())
    // Base64Adapter: serializes byte[] to base64 string, deserializes back
    .create();
```

Additionally, `IntegerPreservingMapDeserializer` is registered on the base GsonProvider to prevent Gson's default behavior of converting all JSON numbers to Double (preserves `42` as Integer, not `42.0`).

### C.12d Property Merge Algorithm (Exact Code)

```java
// CondorPort.propertiesFromJsonString(json):
public P propertiesFromJsonString(String json) {
    // Parse incoming JSON to JsonObject
    JsonObject incoming = gson.fromJson(json, JsonObject.class);

    // Get current cached properties as JsonObject
    JsonObject existing = gson.toJsonTree(mCachedProperties, propertiesType).getAsJsonObject();

    // Merge: for each key in incoming, overwrite in existing
    // JsonObject.add() replaces the value if key exists, adds if not
    for (String key : incoming.keySet()) {
        JsonElement value = incoming.get(key);
        if (!(value instanceof JsonNull)) {  // skip null values
            existing.add(key, value);
        }
    }

    // Deserialize merged JsonObject back to P
    return gson.fromJson(existing, propertiesType);
}
```

Example:
```
Cached:   {"temp": 180, "status": "cooking", "time": 600}
Incoming: {"temp": 200, "cur_time": 295}
Merged:   {"temp": 200, "status": "cooking", "time": 600, "cur_time": 295}
// "temp" overwritten, "status"/"time" preserved, "cur_time" added
```

### C.12e LAN Subscribe: UDP Port Wiring

```
LanCommunicationStrategy.subscribe(portName, productId, ttl, handler):
  |
  +-> localSubscriptionHandler.enableSubscription(networkNode, listeners)
  |     +-> UdpEventReceiver.startReceivingEvents(this)
  |     +-> returns boundSubscriptionUdpPort (e.g., 8080)
  |
  +-> if boundSubscriptionUdpPort == -1:
  |     handler.onError(NO_TRANSPORT_AVAILABLE, "Could not register UDP port")
  |     return
  |
  +-> exchangeKeyIfNecessary(networkNode)
  +-> createSubscribeRequest(portName, productId, ttl, udpPort, handler)
  |     // SubscribeRequest builds: {"subscriber":"deadbeef...","ttl":300,"changeudp":8080}
  |     // Also wraps handler to auto-retry on 401 (same as other requests)
  |
  +-> On FailedToInitUDPSocketException:
        handler.onError(IOEXCEPTION, exception.getMessage())
```

### C.12f LAN Unsubscribe: UDP Thread Lifecycle

```
LanCommunicationStrategy.unsubscribe(portName, productId, handler):
  |
  +-> if subscriptionEventListeners.isEmpty():
  |     // This was the last port subscribed via this strategy
  |     localSubscriptionHandler.disableSubscription()
  |       +-> UdpEventReceiver.stopReceivingEvents(this)
  |             +-> Remove listener
  |             +-> If no listeners left: stop UdpReceivingThread
  |
  +-> exchangeKeyIfNecessary(networkNode)
  +-> DELETE /di/v1/products/{id}/{port} with {"subscriber":"deadbeef..."}
```

Multiple CondorPorts sharing the same LanCommunicationStrategy share one UDP thread. The thread only stops when ALL ports have unsubscribed.

### C.12g CondorPort Callback Batching

```
If getProperties() is called 3 times while a request is already in flight:

  Call 1: getProperties(callback_A)
    -> getPropertiesCallbacks = [callback_A]
    -> tryToPerformNextRequest() -> isRequestInProgress=true -> performGetProperties()
       (HTTP request in flight...)

  Call 2: getProperties(callback_B)
    -> getPropertiesCallbacks = [callback_A, callback_B]
    -> tryToPerformNextRequest() -> isRequestInProgress=true -> return (skip)

  Call 3: getProperties(callback_C)
    -> getPropertiesCallbacks = [callback_A, callback_B, callback_C]
    -> tryToPerformNextRequest() -> isRequestInProgress=true -> return (skip)

  HTTP response arrives:
    -> flushGetPropertiesCallbacks(SuccessResult(data))
       -> callback_A.accept(result)
       -> callback_B.accept(result)  // ALL get the same response
       -> callback_C.accept(result)
    -> requestCompleted() -> isRequestInProgress=false
    -> tryToPerformNextRequest()  // process next queued op (if any)
```

putProperties works differently: each call is queued individually (ConcurrentLinkedQueue), executed one by one in order.

### C.12h Decryption Failure During Subscription

```
LocalSubscriptionHandler.onUDPEventReceived(data, portName, senderIP):
  +-> crypto.decryptData(data) returns null (decryption failed)
  +-> postSubscriptionEventDecryptionFailureOnUiThread(portName, listeners)
        |
        v [on main thread]
      CondorPort.AnonymousClass1.onSubscriptionEventDecryptionFailed(portName):
        +-> if pathMatchesMyPort(portName):
              1. notifyPortSubscriptionListenersOnError(IOEXCEPTION, "decryption failed")
              2. getProperties(callback)  // FULL RELOAD as fallback
                   -> on success: notifyPortSubscriptionListenersOnUpdate()
                   // This recovers the correct state after a decryption failure
```

### C.12i CondorPort Type Resolution

```java
// CondorPort resolves its generic type P at construction time:
private Type determineCondorPortPropertiesType() {
    // Walk up the class hierarchy
    for (Class<?> cls = getClass(); cls != null; cls = cls.getSuperclass()) {
        Type superclass = cls.getGenericSuperclass();
        if (superclass instanceof ParameterizedType) {
            Type[] typeArgs = ((ParameterizedType) superclass).getActualTypeArguments();
            if (typeArgs.length > 0) return typeArgs[0];
        }
    }
    return null; // -> throws IllegalArgumentException
}
// Example: DevicePort extends CondorPort<DevicePortProperties>
//   -> propertiesType = DevicePortProperties.class
//   -> All JSON deserialization uses this type
```

### C.12j Correction: processByteArrayToJsonString Does NOT Decrypt

The doc previously implied LanCommunicationStrategy overrides `processByteArrayToJsonString()` to add AES decryption. This is **wrong**.

Actual behavior:
- `ObservableCommunicationStrategy.processByteArrayToJsonString(bytes)` just does `new String(bytes, UTF_8)` (plain UTF-8)
- LanCommunicationStrategy does **NOT** override this method
- AES decryption happens ONLY in `LocalSubscriptionHandler.onUDPEventReceived()` for subscription events
- For HTTP responses, `LanRequest.execute()` reads the body as `response.body().string()` which is already a string
- If the device encrypts HTTP responses, the body is a base64 string that needs decryption **before** being passed to `processResponse()`. This happens in the `ResponseHandler.onSuccess()` chain, not in `processByteArrayToJsonString()`
- The `Crypto.decryptData()` listener triggers key re-exchange on failure

### C.13 HSDP Token System (Complete)

This section documents every detail of the HSDP token lifecycle.

#### C.13.1 Token Types

| Token | Source | Purpose | Lifetime |
|-------|--------|---------|----------|
| **Bootstrap accessToken** | IAM `getAccessToken(bootstrapClientId, bootstrapClientSecret)` | Temporary for discovery + provisioning. Client credentials grant. | Short |
| **Provisioned accessToken** | IAM `getAccessToken(clientId, clientSecret, username, password)` | Main API token. Resource owner password grant. | `expiresIn` seconds |
| **refreshToken** | Returned with provisioned accessToken | Silent refresh without password | Longer than accessToken |
| **signedToken** | Returned with provisioned accessToken | JWT for MQTT Custom Authorizer | Same as accessToken |

#### C.13.2 Credential Data Structures

```
HSDPBootstrapCredentials (app-wide, hardcoded):
  clientId:     app-level OAuth client ID
  clientSecret: app-level OAuth client secret

HSDPIdentity (device-specific, from provisioning):
  clientId:           OAuth client ID for this identity
  clientSecret:       OAuth client secret for this identity
  username:           Login ID from provisioning (loginId)
  password:           Password from provisioning
  hsdpIdentifier:     HSDP device/identity ID
  identitySignature:  Signature for identity verification

HSDPTokenSet (cached, refreshable):
  accessToken:          Bearer token for API calls
  refreshToken:         For silent refresh (nullable)
  signedToken:          JWT for MQTT auth
  accessTokenExpiresIn: Seconds until expiry

HSDPConfiguration interface provides:
  basePathForIAMService:       "https://iam.{region}.philips-healthsuite.com"
  basePathForDiscoveryService: "https://discovery.{region}.philips-healthsuite.com"
  provisioningEvidence:        Map<String, Object> (app-provided proof)
  customHsdpIdentifier:        Optional override for hsdpId
```

#### C.13.3 Full Sign-On Decision Tree

```
signOn(callback):
  |
  +-> Has cached tokenSet?
  |     |
  |     YES: [RETURNING USER]
  |     |  Apply tokens to PassiveRefreshPolicy (overrideTokens=true)
  |     |  -> discoverServices()
  |     |  -> restartExpiryTimer(tokenSet.accessTokenExpiresIn)
  |     |  -> callback.complete(null)
  |     |
  |     NO: Has provisionedIdentity?
  |           |
  |           YES: [HAS IDENTITY, NEEDS TOKENS]
  |           |  provisionedSignOn():
  |           |    Check cache: remaining > 120s? Use cached.
  |           |    Else: iamService.getAccessToken(
  |           |      identity.clientId, identity.clientSecret,
  |           |      identity.username, identity.password)
  |           |      // grant_type=password
  |           |    -> store provisionedTokenResponse in memory
  |           |    -> compute absolute expiry: expiresIn + (now / 1000)
  |           |    -> persistAccessTokens() to HSDPTokenSet
  |           |    -> discoverServices()
  |           |    -> restartExpiryTimer(expiresIn)
  |           |    -> callback.complete(null)
  |           |
  |           NO: [BRAND NEW - full bootstrap]
  |              1. bootstrapSignOn():
  |              |    iamService.getAccessToken(
  |              |      bootstrapCredentials.clientId,
  |              |      bootstrapCredentials.clientSecret)
  |              |    // grant_type=client_credentials
  |              |
  |              2. discoverServices():
  |              |    GET {discoveryBasePath}/services
  |              |    -> List<Service> with tag + url pairs
  |              |    -> find "PRV" tag for provisioning URL
  |              |
  |              3. provision():
  |              |    provisioningService.createIdentity(null, evidence)
  |              |    // POST {provisioningUrl}/identity
  |              |    -> returns: oauthClientId, oauthClientSecret,
  |              |       loginId, password, hsdpId, identitySignature
  |              |    -> all 6 must be non-null
  |              |    -> configuration.persistIdentity(HSDPIdentity(...))
  |              |
  |              4. signOn(callback) // RECURSIVE: now has identity
```

#### C.13.4 Token Expiry Timer

```
restartExpiryTimer(expiresInSeconds):
  if expiresInSeconds - 30 < 0:
    -> TOKEN_EXPIRY_TOO_SHORT error to all listeners
    -> DO NOT schedule timer
    return

  Cancel existing timer.
  Schedule at: expiresInSeconds * 1000 ms

  When timer fires:
    if tokenSet available (persisted):
      configuration.refreshAccessTokens()
        // iamService.refreshAccessToken(refreshToken, clientId, clientSecret)
        // grant_type=refresh_token
    else:
      provisionedSignOn() // full re-auth with username/password

    On success: listeners.onAccessTokensRefreshed()
      -> HSDPController: disconnect() + connect() with new tokens
    On error: listeners.onAccessTokensRefreshError(error)
      -> HSDPController: connectionStateListener.onConnectionError(AUTHENTICATION_FAILED)

Constants:
  ACCESS_TOKEN_CACHE_THRESHOLD_SECONDS = 120
    // Don't re-fetch if >120s remaining in provisionedSignOn
  ACCESS_TOKEN_INVALIDATION_WINDOW_SECONDS = 30
    // Minimum time needed for timer to fire before expiry
```

#### C.13.5 How Tokens Are Used for MQTT

```
HSDPController.connect():
  1. authentication.signOn() -> get tokens
  2. createControlService():
       Find "IOT" service from discovered services
       Find URL starting with "wss://"
       Parse: "wss://host/path?topic-prefix=prefix" -> endpoint + topicPrefix
       factory.createControlServiceV1(hsdpId, endpoint, topicPrefix)
  3. accessToken = authentication.getAccessToken()
       // Priority: persisted tokenSet > in-memory provisionedTokenResponse
  4. signedToken = authentication.getSignedToken()
       // Same priority
  5. controlServiceV1.connect(accessToken, signedToken, callback)
       // MQTT WSS connect with accessToken + signedToken as auth
```

#### C.13.6 IAM Service API

```
POST {basePath}/authorize/oauth2/token

  grant_type=client_credentials:
    client_id, client_secret
    -> TokenResponse (accessToken only, no signedToken)

  grant_type=password:
    client_id, client_secret, username, password
    -> TokenResponse (accessToken, refreshToken, signedToken, expiresIn)

  grant_type=refresh_token:
    refresh_token, client_id, client_secret, scope (optional)
    -> TokenResponse (new accessToken, refreshToken, signedToken, expiresIn)
```

#### C.13.7 Discovery and Provisioning APIs

```
Discovery:
  GET {basePath}/services
  -> List<Service> { tag: String, url: String }
  Known tags: "PRV" (provisioning), "IOT" (MQTT control)

Provisioning:
  POST {basePath}/identity
  Body: evidence map
  -> Parameters { oauthClientId, oauthClientSecret,
     loginId, password, hsdpId, identitySignature }
```

#### C.13.8 PassiveRefreshPolicy

```
PassiveRefreshPolicy holds tokens but NEVER auto-refreshes.
refreshToken() always returns NO_REFRESH_TOKEN.

HSDPAuthentication manages refresh manually via its own Timer.
Constructor calls disableAutomaticRefresh():
  factory.applyRefreshPolicy(new PassiveRefreshPolicy(), false)

When returning user signs on:
  factory.applyRefreshPolicy(policyWithTokens, overrideTokens=true)
  All subsequent HTTP requests include the accessToken.
```

### C.12 HSDP Subscription (Cloud Push Events)

```
HSDPCommunicationStrategy.subscribe("air", 1, ttl, handler)
  |
  +-> subscriptionHandler.enableSubscription(networkNode, listeners)
  |     +-> hsdpId = networkNode.getHsdpId()
  |     +-> messenger.registerMessageListener(this)  // Listen for ALL MQTT messages
  |
  +-> requestQueue.addRequest(new HSDPRemoteRequest(
        operation=SUBSCRIBE, hsdpId, productId=1, portName="air",
        data=null, handler, messenger, ttl=300))
        |
        v [HSDPRemoteRequest.execute()]
      Command: {"condorVersion":"1", "op":"Subscribe", "path":"1/air", "ttl":300}
      -> Send via MQTT, wait for acceptance + notification (same as C.8)

[Device state changes - device publishes to MQTT]:
  ControlServiceV1.Listener.onCommandReceived(received)
    -> HSDPController.notifyMessageListeners(received)
      -> HSDPRemoteSubscriptionHandler.messageReceived(received)
           |
           +-> type != "notification": ignore
           +-> command.statusDetail["op"] != "ChangeIndication": ignore
           +-> Parse CondorControlMessage from statusDetailAsJsonString
           +-> postSubscriptionEventOnUiThread(
                 condorMessage.path,          // e.g., "1/air"
                 Gson.toJson(condorMessage.values).getBytes(UTF_8),
                 subscriptionEventListeners)
                   |
                   v [Same flow as LAN subscription events]
                 CondorPort processes and merges data

HSDPCommunicationStrategy.unsubscribe("air", 1, handler)
  +-> subscriptionHandler.disableSubscription()
  |     messenger.unregisterMessageListener(this)
  +-> Send UNSUBSCRIBE command via MQTT
```
