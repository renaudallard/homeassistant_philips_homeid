# Philips HomeID APK - Complete Decompiled Code Analysis

**APK Version:** 8.16.0 (com.philips.ka.oneka.app)
**Decompiler:** JADX
**Analysis Date:** 2026-03-26

This document provides a line-by-line annotated analysis of every relevant subsystem in the decompiled Philips HomeID APK. The goal is to document the complete protocol implementation so it can be reproduced independently.

---

## Table of Contents

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
    public static final String KEY_IP_ADDRESS = "ip_address";    // LAN IP address
    public static final String KEY_IS_PAIRED = "is_paired";      // Pairing state ordinal
    public static final String KEY_LAST_KNOWN_NETWORK = "lastknown_network"; // WiFi SSID when last seen
    public static final String KEY_LAST_PAIRED = "last_paired";  // Timestamp of last pairing
    public static final String KEY_MAC_ADDRESS = "mac_address";  // Device MAC
    public static final String KEY_MISMATCHED_PIN = "mismatched_pin"; // Certificate pin that didn't match stored pin
    public static final String KEY_MODEL_ID = "model_id";        // Model number from discovery
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

    // Reset OkHttp client timeouts to 30ms (actually 30s)
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
            // Step 1: Trim whitespace from the data
            String trimmed = data.trim();

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
    -> trim whitespace
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

The cloud authentication layer wraps credential providers.

```java
// Provides:
// - getMqttConnectionInfo(): Fetches MQTT credentials (access token + signature + WSS URL)
// - getUserId() / federatedUserId(): Gets user identity
// - clearUserData(): Invalidates all cached tokens
// - invalidateMqttConnectionInfo(): Forces MQTT credential refresh

// All methods return RxJava Single/Completable types
// tokenProviderRef holds the current ClientAuthenticationProvider (Gigya/HSDP tokens)
```

---

## 19. MQTT Connection Info

**File:** `cl/daconnect/authentication/models/MqttConnectionInfo.java`

```java
// Data class holding MQTT connection details
public final class MqttConnectionInfo {
    private final String accessToken;       // HSDP access token (value type: AccessToken)
    private final String mqttSignature;     // MQTT auth signature (value type: MqttSignature)
    private final String tenant;            // HSDP tenant (value type: Tenant)
    private final WebSocketUrl webSocketUrl; // WSS endpoint URL

    // These are the credentials needed to connect to AWS IoT via WSS:
    // - accessToken: used as Custom Authorizer token
    // - mqttSignature: used as Custom Authorizer signature
    // - webSocketUrl: the wss:// URL to connect to
    // - tenant: identifies the Philips HSDP environment
}
```

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
| cppid | TEXT UNIQUE | Device identifier |
| client_id | TEXT | Base64 128-bit random key (locally generated) |
| client_secret | TEXT | Device-issued secret (from pairing) |
| encryption_key | TEXT | AES-128 hex key (from `/security` endpoint) |
| hsdpid | TEXT | HSDP cloud device ID |
| pin | TEXT | SHA-256 of TLS public key (base64) |
| is_paired | SMALLINT | 0=PAIRED, 1=NOT_PAIRED, 2=UNPAIRED, 3=PAIRING |
