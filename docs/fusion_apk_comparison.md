# FUSION MQTT: APK vs Integration Comparison

Full side-by-side comparison of the Philips HomeID Android app (APK) and
this Home Assistant integration for FUSION device cloud relay.

APK version analysed: 8.16.0 (`com.philips.ka.oneka.app`)
Integration version: 2.2.4

---

## 1. Authentication Flow

### 1.1 Identity Provider

| Aspect | APK | Integration |
|--------|-----|-------------|
| Provider | Gigya CDC (`cdc.accounts.home.id`) | Same |
| API key | `4_JGZWlP8eQHpEqkvQElolbA` | Same |
| OIDC issuer | `https://cdc.accounts.home.id/oidc/op/v1.0/4_JGZWlP8eQHpEqkvQElolbA` | Same |
| Auth endpoint | `{issuer}/authorize` | Same |
| Token endpoint | `{issuer}/token` | Same |

### 1.2 OAuth / OIDC Parameters

| Parameter | APK | Integration | Match |
|-----------|-----|-------------|-------|
| client_id | `-u6aTznrxp9_9e_0a57CpvEG` | Same | OK |
| redirect_uri | `com.philips.ka.oneka.app.prod://oauthredirect` | Same | OK |
| response_type | `code` | `code` | OK |
| PKCE | S256 code_challenge | S256 code_challenge | OK |
| grant_type (exchange) | `authorization_code` | `authorization_code` | OK |
| grant_type (refresh) | `refresh_token` | `refresh_token` | OK |

### 1.3 OAuth Scopes

| APK | Integration |
|-----|-------------|
| Not directly visible in decompiled code (configured via HSDP discovery) | `openid profile email offline_access DI.Account.read DI.AccountProfile.read DI.AccountProfile.write DI.AccountGeneralConsent.read DI.AccountGeneralConsent.write DI.GeneralConsent.read DI.GeneralConsent.write VoiceProvider.read VoiceProvider.write subscriptions consents profile_extended DI.AccountSubscription.write DI.AccountSubscription.read` |

**Note:** APK scopes could not be extracted from decompiled code (they are
fetched from HSDP discovery at runtime). The integration uses the full
scope set observed from the OneKA app's OAuth flow. If the DaConnect
Custom Authorizer validates scope claims, a mismatch here could cause
MQTT auth failure.

### 1.4 Login Method

| Step | APK | Integration |
|------|-----|-------------|
| User login | In-app WebView with Gigya SDK | Gigya OTP via email |
| OTP send | `accounts.auth.otp.email.sendCode` | Same |
| OTP verify | `accounts.auth.otp.email.login` | Same |
| OAuth dance | AppAuth library (in-process) | Playwright headless Chromium (subprocess) |
| Session transfer | Gigya SDK sets cookies natively | Sets Gigya cookies via Playwright |

### 1.5 Token Storage

| Aspect | APK | Integration |
|--------|-----|-------------|
| Access token | In-memory (`AtomicReference<ClientAuthenticationProvider>`) | In-memory (used immediately, not persisted) |
| Refresh token | EncryptedSharedPreferences / Tink | HA config entry data (encrypted at rest by HA) |
| Token rotation | Automatic via `RefreshHsdpTokenProvider` | Automatic on each FUSION entry setup + reconnect |

---

## 2. DaConnect API Calls

### 2.1 Device List

| Aspect | APK | Integration |
|--------|-----|-------------|
| Endpoint | `https://prod.eu-da.iot.versuni.com/api/da/user/self/device` | Same |
| Method | GET | GET |
| Auth header | `Authorization: Bearer {access_token}` | Same |
| Response fields used | `id`, `ctn`, `macAddress`, `thingName`, `friendlyName` | Same |

### 2.2 MQTT Signature

| Aspect | APK | Integration |
|--------|-----|-------------|
| Endpoint | `https://prod.eu-da.iot.versuni.com/api/da/user/self/signature` | Same |
| Method | GET | GET |
| Auth header | `Authorization: Bearer {access_token}` | Same |
| Response model | `SignatureResponse` with single field `@SerializedName("signature")` | Parses full JSON, uses `sig_data.get("signature", "")` |
| Response `accessToken` field | **Not in response model** (only `signature`) | Ignored since v2.2.4 (was previously used as fallback) |

**Key finding:** The APK's `SignatureResponse` class has exactly ONE field:
`signature`. There is no `accessToken` in the response. Before v2.2.4, the
integration tried `sig_data.get("accessToken", access_token)` which could
have used a different token if the API happened to return one.

### 2.3 Thing Name Resolution

| Aspect | APK | Integration |
|--------|-----|-------------|
| Source | `thingName` field from device list response | Same (via `get_thing_name()`) |
| Lookup | By device ID or MAC address | Same |
| Note | `thingName != externalDeviceId` (separate fields) | Same understanding |

---

## 3. MQTT Connection

### 3.1 Transport

| Aspect | APK | Integration | Match |
|--------|-----|-------------|-------|
| Protocol | MQTT 3.1.1 over WebSocket Secure | Same | OK |
| URL | `wss://ats.prod.eu-da.iot.versuni.com:443/mqtt` | Same | OK |
| Port | 443 | 443 | OK |
| WebSocket path | `/mqtt` | `/mqtt` | OK |
| TLS | System default SSLContext (no custom CA, no client cert) | `ssl.CERT_REQUIRED`, `ssl.PROTOCOL_TLS` (system CA store) | OK |
| Port 8883 | Not used (requires mTLS, no client certs in APK) | Not used (confirmed via connectivity test) | OK |

### 3.2 WebSocket Upgrade Headers

| Header | APK | Integration | Match |
|--------|-----|-------------|-------|
| `x-amz-customauthorizer-name` | `CustomAuthorizer` | `CustomAuthorizer` | OK |
| `x-amz-customauthorizer-signature` | `{mqttSignature}` from signature API | `{mqtt_signature}` from signature API | OK |
| `token-header` | `Bearer {accessToken}` (OIDC token) | `Bearer {access_token}` (OIDC token, fixed in v2.2.4) | OK |
| `tenant` | `{tenant}` from FusionConfiguration | `{device.tenant}` | OK |
| `content-type` | `application/json` | `application/json` | OK |

### 3.3 MQTT Client Options

| Option | APK | Integration | Match |
|--------|-----|-------------|-------|
| Client library | Eclipse Paho Java (`org.eclipse.paho.client.mqttv3`) | paho-mqtt Python v2 | Different impl, same protocol |
| Client ID format | `{userId}_{UUID}` (userId = JWT `sub` claim) | `{user_id}_{UUID}` (user_id = JWT `sub` claim, fixed in v2.2.4) | OK |
| clean_session | `false` | `False` (fixed in v2.2.3) | OK |
| keepalive | 30 seconds | 30 seconds | OK |
| connection timeout | 10 seconds | 30 seconds | OK (ours more lenient) |
| auto reconnect | `setAutomaticReconnect(true)` (Paho built-in) | `reconnect_delay_set(1, 60)` + manual credential refresh | OK |
| persistence | `MemoryPersistence` (in-memory Hashtable) | None (paho-mqtt default) | Minor diff |
| WebSocket impl | OkHttp (via Java Paho) | Custom (paho-mqtt Python built-in raw socket) | Different impl |

### 3.4 Token Used for MQTT

| Aspect | APK | Integration (v2.2.4) |
|--------|-----|----------------------|
| Source | `ClientAuthenticationProvider.getAccessToken()` = OIDC access_token | `access_token` from `refresh_tokens()` = OIDC access_token |
| Is it from signature response? | **No.** Signature response only has `signature` field. | **No.** Fixed in v2.2.4. Previously tried `sig_data.get("accessToken")`. |

---

## 4. MQTT Topics

| Topic | APK | Integration | Match |
|-------|-----|-------------|-------|
| Shadow get | `$aws/things/{thingName}/shadow/get` | Same | OK |
| Shadow get accepted | `$aws/things/{thingName}/shadow/get/accepted` | Same | OK |
| Shadow get rejected | `$aws/things/{thingName}/shadow/get/rejected` | Same | OK |
| Shadow update | `$aws/things/{thingName}/shadow/update` | Same | OK |
| Shadow update accepted | `$aws/things/{thingName}/shadow/update/accepted` | Same | OK |
| Shadow update rejected | `$aws/things/{thingName}/shadow/update/rejected` | Same | OK |
| NCP commands (to device) | `{tenant}_ctrl/{thingName}/to_ncp` | Same | OK |
| NCP responses (from device) | `{tenant}_ctrl/{thingName}/from_ncp` | Same | OK |

### 4.1 Subscription QoS

| Topic type | APK | Integration | Match |
|------------|-----|-------------|-------|
| Shadow subscriptions | QoS 0 | QoS 0 | OK |
| NCP from_ncp | QoS 0 | QoS 0 | OK |

### 4.2 Publish QoS

| Operation | APK | Integration | Match |
|-----------|-----|-------------|-------|
| Shadow get request | QoS 1 | QoS 1 | OK |
| Shadow update | QoS 1 | QoS 1 | OK |
| NCP to_ncp commands | QoS 1 | QoS 1 | OK |

---

## 5. NCP Command Protocol

### 5.1 Command Format

| Field | APK | Integration | Match |
|-------|-----|-------------|-------|
| `cid` | 8-char hex (from `CorrelationId`) | `secrets.token_bytes(4).hex()` (8-char hex) | OK |
| `time` | `yyyy-MM-dd'T'HH:mm:ss'Z'` (no fractional seconds) | `datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")` | OK |
| `type` | `"command"` | `"command"` | OK |
| `cn` | `"updatePort"` or `"getPort"` | Same | OK |
| `ct` | `"mobile"` | `"mobile"` | OK |
| `data.portName` | Device-specific (`airfryer`, `venusaf`, etc.) | Same | OK |
| `data.properties` | Dict of property key-values | Same | OK |

### 5.2 Shadow Control

| Operation | APK | Integration | Match |
|-----------|-----|-------------|-------|
| Power on/off | `{"state": {"desired": {"powerOn": true/false}}}` on shadow/update | Same | OK |
| State request | `{}` on shadow/get | Same | OK |

---

## 6. Reconnection

### 6.1 Transient Disconnects

| Aspect | APK | Integration |
|--------|-----|-------------|
| Mechanism | Paho Java `setAutomaticReconnect(true)` | paho-mqtt `reconnect_delay_set(1, 60)` |
| Credentials | Reuses existing (same WebSocket headers) | Reuses existing (same headers) |
| Delay | Paho built-in exponential backoff | 1s to 60s |

### 6.2 Token Expiry

| Aspect | APK | Integration |
|--------|-----|-------------|
| Detection | Disconnect triggers credential invalidation | `_on_disconnect` with non-zero reason_code |
| Refresh | `DaAuthenticationService` re-fetches tokens + signature | `_refresh_mqtt_credentials()` calls `refresh_tokens()` + `get_mqtt_signature()` |
| Backoff | Via `MQTTConnectionConfiguration.retryConfiguration` | 1s, 1.5s, 2.25s... up to 60s, max 5 retries |

---

## 7. Platform Differences

| Aspect | APK | Integration |
|--------|-----|-------------|
| MQTT library | Eclipse Paho Java (`org.eclipse.paho.client.mqttv3`) | paho-mqtt Python v2.1.0 |
| WebSocket layer | OkHttp (mature, well-tested) | paho-mqtt built-in (raw socket HTTP upgrade) |
| TLS library | Android system (BoringSSL) | Python ssl module (OpenSSL) |
| Event loop | Android Handler/Looper + Kotlin Coroutines | asyncio + executor threads |
| Persistence | `MemoryPersistence` (Hashtable) | None |

---

## 8. Known Remaining Differences

### 8.1 Potentially Significant

- **OIDC scopes**: APK scopes are loaded from HSDP discovery at runtime
  and could not be extracted from the decompiled code. If the Custom
  Authorizer validates scope claims in the JWT, a scope mismatch could
  cause authentication failure.

- **HSDP bridge**: The APK may perform an additional HSDP token exchange
  after the Gigya CDC OIDC flow, producing a different access token.
  Classes like `HsdpAuthorizationCodeTokenProvider` and
  `RefreshHsdpTokenProvider` suggest a secondary token layer. Our
  integration uses the Gigya CDC OIDC tokens directly.

### 8.2 Unlikely to Matter

- **MemoryPersistence**: APK explicitly uses `MemoryPersistence` for the
  Paho client. paho-mqtt Python uses no persistence by default. Both
  result in in-memory-only message storage.

- **Connection timeout**: APK uses 10s, we use 30s. More lenient is
  fine.

- **WebSocket implementation**: Different libraries but same HTTP/1.1
  upgrade protocol. Both send `Sec-WebSocket-Protocol: mqtt`.

---

## 9. Version History of Fixes

| Version | Fix | APK reference |
|---------|-----|---------------|
| 2.2.1 | Added debug logging, increased timeout to 30s | - |
| 2.2.2 | Added port 8883 fallback (later removed) | Port 8883 confirmed mTLS-only |
| 2.2.3 | `clean_session=False`, UUID client ID, auto-reconnect | `setCleanSession(false)`, `{userId}_{UUID}`, `setAutomaticReconnect(true)` |
| 2.2.4 | JWT `sub` for client ID, always use OIDC token | `getUserId()` = `sub` claim, `ClientAuthenticationProvider.getAccessToken()` |

---

## 10. APK Source Files Reference

| File | Purpose |
|------|---------|
| `DaMqttClientImpl.smali` | MQTT client: connect, subscribe, publish, headers |
| `MqttConnectionInfo.smali` | Data class: accessToken, mqttSignature, tenant, webSocketUrl |
| `SignatureResponse.smali` | API response: single `signature` field (@SerializedName) |
| `FusionConfiguration.smali` | Platform URLs: REST, MQTT, tenant |
| `DomainBuilderKt.smali` | Hardcoded defaults: `prod.eu-da.iot.versuni.com`, `da`, `ats.prod.eu-da.iot.versuni.com` |
| `WebSocketUrl.smali` | URL construction: `wss://{host}:443/mqtt` |
| `DaIoTCredentialsProvider.smali` | Abstract credential interface: getUserId, getMqttConnectionInfo |
| `DaAuthenticationService.smali` | Orchestrator: token provider + signature provider |
| `HsdpAuthorizationCodeTokenProvider.smali` | Initial OIDC token acquisition via HSDP |
| `RefreshHsdpTokenProvider.smali` | Token refresh via HSDP |
| `DaIoTServiceClient.smali` | REST API client for device list |

## 11. Integration Source Files Reference

| File | Purpose |
|------|---------|
| `mqtt_api.py` | MQTT client: connect, subscribe, publish, headers, reconnect |
| `__init__.py` | FUSION entry setup: token refresh, signature, MQTT client creation |
| `cloud_api.py` | All cloud API calls: OIDC, signature, devices, thing name |
| `config_flow.py` | FUSION entry creation: detect cloud-only devices, resolve thingName |
| `const.py` | All constants: OIDC config, FUSION defaults, endpoints |
| `coordinator.py` | DataUpdateCoordinator: bridges MQTT state to HA entities |
