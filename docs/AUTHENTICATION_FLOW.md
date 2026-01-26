# Philips HomeID Authentication Flow - Complete Technical Documentation

This document describes the complete authentication flow used by the Philips HomeID mobile app (v8.16.0), based on decompiled APK analysis.

## Table of Contents

1. [Overview](#overview)
2. [OAuth/OIDC Configuration](#oauthoidc-configuration)
3. [Authentication Flow Sequence](#authentication-flow-sequence)
4. [Token Types and Usage](#token-types-and-usage)
5. [SAS Token Exchange](#sas-token-exchange)
6. [IoT API Access](#iot-api-access)
7. [Key Endpoints](#key-endpoints)
8. [Implementation Notes](#implementation-notes)

---

## Overview

The Philips HomeID app uses a multi-stage authentication flow:

1. **Gigya CDC OIDC** - User authenticates via Gigya's OIDC provider
2. **AppAuth Library** - Handles OAuth 2.0 + PKCE flow
3. **SAS Token Exchange** - Exchanges OIDC tokens for HSDP tokens
4. **IoT API Access** - Uses HSDP tokens to access device APIs

**Critical Insight**: The app uses **pure OIDC browser-based flow** with consent. It does NOT use Gigya's `accounts.getJWT` or OTP APIs for the main authentication - those produce tokens with `aud=None` that the SAS API rejects.

---

## OAuth/OIDC Configuration

### Gigya CDC OIDC Provider

| Parameter | Value |
|-----------|-------|
| **OIDC Issuer** | `https://cdc.accounts.home.id/oidc/op/v1.0/4_JGZWlP8eQHpEqkvQElolbA` |
| **Authorization Endpoint** | `{issuer}/authorize` |
| **Token Endpoint** | `{issuer}/token` |
| **Gigya API Key** | `4_JGZWlP8eQHpEqkvQElolbA` |

### OAuth Client Configuration

| Parameter | Value |
|-----------|-------|
| **Client ID** | `-u6aTznrxp9_9e_0a57CpvEG` |
| **Redirect URI** | `com.philips.ka.oneka.app.prod://oauthredirect` |
| **Response Type** | `code` |
| **Code Challenge Method** | `S256` (SHA-256 for PKCE) |

### OAuth Scopes

The app requests these scopes:

```
openid
profile
email
offline_access
DI.Account.read
DI.AccountProfile.read
DI.AccountProfile.write
DI.AccountGeneralConsent.read
DI.AccountGeneralConsent.write
DI.GeneralConsent.read
DI.GeneralConsent.write
VoiceProvider.read
VoiceProvider.write
subscriptions
consents
profile_extended
DI.AccountSubscription.write
DI.AccountSubscription.read
```

**Minimum required for device access**: `openid profile email offline_access`

### Alternative Client (Nutriu/Alexa Integration)

| Parameter | Value |
|-----------|-------|
| **Client ID** | `21e431131cb04a0eb56` |
| **Client Secret** | `@@3f2.6lo21_2F61` |
| **Redirect URI** | `com.philips.apps.nutriu.21e431131cb04a0eb56://oauthredirect` |

---

## Authentication Flow Sequence

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    COMPLETE AUTHENTICATION FLOW                              │
└─────────────────────────────────────────────────────────────────────────────┘

Step 1: OIDC DISCOVERY
════════════════════════
GET https://cdc.accounts.home.id/oidc/op/v1.0/4_JGZWlP8eQHpEqkvQElolbA/.well-known/openid-configuration

Response contains:
  - authorization_endpoint
  - token_endpoint
  - userinfo_endpoint
  - etc.


Step 2: AUTHORIZATION REQUEST (Browser)
═══════════════════════════════════════
GET {authorization_endpoint}
  ?client_id=-u6aTznrxp9_9e_0a57CpvEG
  &response_type=code
  &redirect_uri=com.philips.ka.oneka.app.prod://oauthredirect
  &scope=openid profile email offline_access
  &state={random_state}
  &code_challenge={SHA256(code_verifier)}
  &code_challenge_method=S256
  &ui_locales={locale}

User logs in and consents → Redirect to:
  com.philips.ka.oneka.app.prod://oauthredirect?code={auth_code}&state={state}


Step 3: TOKEN EXCHANGE
══════════════════════
POST {token_endpoint}
Content-Type: application/x-www-form-urlencoded

  client_id=-u6aTznrxp9_9e_0a57CpvEG
  &grant_type=authorization_code
  &code={auth_code}
  &redirect_uri=com.philips.ka.oneka.app.prod://oauthredirect
  &code_verifier={code_verifier}

Response:
{
  "access_token": "{OIDC_access_token}",
  "id_token": "{OIDC_id_token}",
  "refresh_token": "{OIDC_refresh_token}",
  "token_type": "Bearer",
  "expires_in": 300
}


Step 4: SAS TOKEN EXCHANGE
══════════════════════════
POST https://www.home.id/api/sas/hsdp-token
  (or https://www.backend.vbs.versuni.com/api/sls/hsdp/token)

Headers:
  Authorization: Bearer {OIDC_access_token}    ← CRITICAL: Uses access_token!
  Accept: application/vnd.oneka.v2.0+json
  Content-Type: application/vnd.oneka.v2.0+json

Body:
{
  "idToken": "{OIDC_id_token}",                 ← Uses id_token in body
  "exchangeFor": "HSDP"
}

Response:
{
  "accessToken": "{HSDP_access_token}",
  "refreshToken": "{HSDP_refresh_token}",
  "signedToken": "{HSDP_signed_token}",
  "idToken": "{HSDP_id_token}",
  "expiresIn": 3600,
  "tokenType": "Bearer",
  "federationFlow": "..."
}


Step 5: IOT API ACCESS
══════════════════════
GET https://air.acc.eu-da.iot.versuni.com/api/user/self/device

Headers:
  Authorization: Bearer {HSDP_access_token}
  Accept: application/json

Response: List of user's devices with credentials
```

---

## Token Types and Usage

### Token Chain

```
Gigya OIDC Provider
        │
        ▼
┌───────────────────┐
│  OIDC Tokens      │
│  - access_token   │──────► SAS API Authorization header
│  - id_token       │──────► SAS API body (for exchange)
│  - refresh_token  │──────► Token refresh
└───────────────────┘
        │
        ▼ (SAS Token Exchange)
┌───────────────────┐
│  HSDP Tokens      │
│  - accessToken    │──────► IoT API Authorization header
│  - signedToken    │──────► Some API calls
│  - refreshToken   │──────► Token refresh
└───────────────────┘
```

### Token Storage in App

| Storage Key | Content |
|-------------|---------|
| `STATE` | Serialized AppAuth AuthState (contains OIDC tokens) |
| `DI_DA_AUTH_SERVICE_CONFIGURATION` | OIDC discovery configuration |
| HSDP Token Storage | HSDP tokens from SAS exchange |

### Token Retrieval Chain (Code)

```java
// To get OIDC access_token:
PhilipsUserImpl.l()
  → DiDaBridgeImpl.g()
    → AuthStateManager.c()
      → AuthState.f()  // Returns access_token

// To get OIDC id_token:
PhilipsUserImpl.u()
  → DiDaBridgeImpl.g()
    → AuthStateManager.c()
      → AuthState.j()  // Returns id_token
```

---

## SAS Token Exchange

### Request Details

**Endpoint URLs** (in order of preference):
1. From discovery service: `{discovered_url}`
2. Fallback 1: `https://www.home.id/api/sas/hsdp-token`
3. Fallback 2: `https://www.backend.vbs.versuni.com/api/sls/hsdp/token`

**HTTP Method**: POST

**Headers**:
```http
Authorization: Bearer {OIDC_access_token}
Accept: application/vnd.oneka.v2.0+json
Content-Type: application/vnd.oneka.v2.0+json
```

**Request Body**:
```json
{
  "idToken": "{OIDC_id_token}",
  "exchangeFor": "HSDP"
}
```

**Response Body**:
```json
{
  "accessToken": "string",
  "refreshToken": "string",
  "signedToken": "string",
  "idToken": "string",
  "expiresIn": 3600,
  "tokenType": "Bearer",
  "federationFlow": "string"
}
```

### Critical Requirements

1. **Authorization Header MUST use OIDC access_token** (NOT id_token, NOT session_token)
2. **Body MUST contain OIDC id_token** with valid `aud` claim
3. **id_token `aud` claim MUST NOT be null** - tokens from `accounts.getJWT` have `aud=None` and are rejected

---

## IoT API Access

### Base URL

```
https://air.acc.eu-da.iot.versuni.com/api/
```

### Device Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `user/self/device` | GET | Get user's devices |
| `user/self/device/migration` | POST | Get device credentials (migration API) |
| `user/self/home` | GET | Get user's homes |

### Authorization

All IoT API requests use:
```http
Authorization: Bearer {HSDP_access_token}
```

The `SasAuthorizationInterceptor` automatically adds this header to all requests.

---

## Key Endpoints

### Backend Configuration

| Service | URL |
|---------|-----|
| Backend API | `https://www.backend.vbs.versuni.com/api/` |
| Home.ID API | `https://www.home.id/api/` |
| Home.ID Web | `https://www.home.id/` |
| DiDa Auth UI | `www.accounts.home.id/authui` |
| HSDP IAM | `https://iam-service.eu-west.philips-healthsuite.com` |
| HSDP Discovery | `https://discovery.eu01.iot.hsdp.io/core/discovery` |
| IoT API | `https://air.acc.eu-da.iot.versuni.com/api/` |

### HSDP IAM OAuth2 Endpoints

| Endpoint | URL |
|----------|-----|
| Token | `https://iam-service.eu-west.philips-healthsuite.com/authorize/oauth2/token` |
| Authorize | `https://iam-service.eu-west.philips-healthsuite.com/authorize/oauth2/authorize` |

---

## Implementation Notes

### Why OTP Flow Fails for Device Access

The email OTP flow uses Gigya's `accounts.auth.otp.email.login` which:
1. Returns a session token (not OIDC tokens)
2. Using `accounts.getJWT` with this session produces tokens with `aud=None`
3. The SAS API validates the `aud` claim and rejects tokens with null audience

**The OTP flow bypasses OAuth consent**, so it cannot produce valid OIDC tokens that work with the SAS API.

However, the OTP flow CAN be used to get account information and list devices in the cloud. It just cannot retrieve device credentials via the SAS/HSDP API.

### Why Device Authorization Flow Fails

The Device Authorization Grant (RFC 8628) requires server configuration:
```
{"error":"invalid_request","error_description":"Missing Configuration - Device Flow Proxy Page is missing"}
```

Philips has not configured this on their Gigya CDC instance.

### Why Programmatic OAuth Consent is Impossible

We tested multiple approaches to programmatically obtain valid OIDC tokens:

1. **`accounts.oauth.authorize`** - Returns "Unauthorized user" / "No cookie"
2. **`fidm.oidc.op.authorize`** - Returns 503 Service Unavailable
3. **`fidm.oidc.op.getToken`** - Returns 403005 "Unauthorized user"
4. **Consent APIs** (`accounts.setAccountConsent`, `accounts.auth.setConsentStatus`) - Permission denied or 404

The fundamental issue is that OAuth consent MUST be granted by the user in an interactive browser session. This is an intentional security feature of the OAuth protocol - it ensures the user explicitly authorizes the application.

### Working Solution: Browser OAuth Flow

The only working flow is the **browser-based OAuth Authorization Code flow with PKCE**:

1. Open browser to authorization URL
2. User logs in and consents
3. Browser tries to redirect to `com.philips.ka.oneka.app.prod://oauthredirect?code=...`
4. Browser blocks the redirect (custom mobile app scheme)
5. User manually captures the blocked URL from browser DevTools (Network tab → Location header)
6. Extract the `code` from the redirect URL
7. Exchange code for tokens at token endpoint
8. Exchange OIDC tokens for HSDP tokens via SAS API

### Capturing the Redirect URL

Since the redirect URI uses a mobile app custom scheme (`com.philips.ka.oneka.app.prod://`), browsers cannot navigate to it. Users must manually capture the URL:

1. Open browser Developer Tools (F12) before or during login
2. Go to Network tab
3. After accepting consent, look for a request to `authorize/continue`
4. The request will fail/redirect - check Response Headers → Location
5. Copy the full `com.philips.ka.oneka.app.prod://oauthredirect?code=...` URL
6. Extract the `code` parameter value

### Token Refresh

OIDC tokens can be refreshed using:
```http
POST {token_endpoint}
Content-Type: application/x-www-form-urlencoded

client_id=-u6aTznrxp9_9e_0a57CpvEG
&grant_type=refresh_token
&refresh_token={refresh_token}
```

---

## Source Files Reference

| Component | File Path |
|-----------|-----------|
| OAuth Configuration | `sources/com/philips/ka/oneka/di/da/repository/DiDaRepository.java:1153` |
| Scopes | `sources/com/philips/ka/oneka/di/da/constants/DiDaConstants.java:18` |
| Backend Config | `sources/com/philips/ka/oneka/app/multimodule/backend/BackendConfigKt.java:22` |
| SAS API Service | `sources/com/philips/ka/oneka/backend/other/SasApiService.java:16-18` |
| SAS Auth Interceptor | `sources/com/philips/ka/oneka/backend/other/SasAuthorizationInterceptor.java:24-30` |
| Token Request | `sources/com/philips/ka/oneka/backend/data/response/GetSasHsdpTokenDataRequest.java` |
| Token Response | `sources/com/philips/ka/oneka/backend/data/response/SasHsdpTokensResponse.java` |
| DiDa Bridge | `sources/com/philips/ka/oneka/di/da/DiDaBridgeImpl.java:155-168` |
| PhilipsUser | `sources/com/philips/ka/oneka/domain/philips_user/PhilipsUserImpl.java:521-527` |
| AuthState | `sources/net/openid/appauth/a.java` |
| IoT API Base | `sources/com/philips/ka/oneka/backend/di/module/OthersApiModule.java:76` |

---

## Summary

The Philips HomeID app authentication requires:

1. **Browser-based OAuth flow** (cannot be bypassed with OTP)
2. **PKCE with S256** code challenge
3. **Valid OIDC tokens** with proper `aud` claim
4. **Two-stage token exchange**: OIDC → SAS → HSDP
5. **Specific headers** for SAS API: `application/vnd.oneka.v2.0+json`
6. **Authorization header uses access_token**, body uses id_token for SAS exchange
