#!/usr/bin/env python3
"""
Test script to automate Philips HomeID authentication flow.
This attempts to get proper OIDC tokens using the OTP session.
"""
import urllib.request
import urllib.parse
import json
import hashlib
import base64
import secrets
import sys

GIGYA_API_KEY = "4_JGZWlP8eQHpEqkvQElolbA"
GIGYA_API_URL = "https://cdc.accounts.home.id"
OAUTH_CLIENT_ID = "-u6aTznrxp9_9e_0a57CpvEG"
REDIRECT_URI = "com.philips.ka.oneka.app.prod://oauthredirect"
OIDC_ISSUER = f"https://cdc.accounts.home.id/oidc/op/v1.0/{GIGYA_API_KEY}"


def make_request(url, data=None, headers=None):
    """Make HTTP request and return JSON response."""
    if data and isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()

    req = urllib.request.Request(url, data=data)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read().decode())


def request_otp(email):
    """Request OTP to be sent to email."""
    print(f"\n=== Step 1: Request OTP for {email} ===")
    result = make_request(
        f"{GIGYA_API_URL}/accounts.auth.otp.email.sendCode",
        {"email": email, "apiKey": GIGYA_API_KEY, "format": "json"}
    )

    if result.get('errorCode') != 0:
        raise Exception(f"OTP request failed: {result.get('errorMessage')}")

    print(f"OTP sent successfully!")
    return result.get('vToken')


def verify_otp(email, code, vtoken):
    """Verify OTP and get session token."""
    print(f"\n=== Step 2: Verify OTP ===")
    result = make_request(
        f"{GIGYA_API_URL}/accounts.auth.otp.email.login",
        {
            "email": email,
            "code": code,
            "vToken": vtoken,
            "apiKey": GIGYA_API_KEY,
            "format": "json"
        }
    )

    if result.get('errorCode') != 0:
        raise Exception(f"OTP verification failed: {result.get('errorMessage')}")

    session_token = result.get('sessionInfo', {}).get('cookieValue')
    uid = result.get('UID')
    uid_sig = result.get('UIDSignature')
    sig_ts = result.get('signatureTimestamp')

    print(f"Login successful!")
    print(f"  UID: {uid[:30]}...")
    print(f"  Session token: {session_token[:50]}...")

    return {
        'session_token': session_token,
        'uid': uid,
        'uid_signature': uid_sig,
        'signature_timestamp': sig_ts
    }


def try_oidc_authorize(session_token):
    """Try to get authorization code using session token."""
    print(f"\n=== Step 3: Try OIDC authorize with session ===")

    # Generate PKCE
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b'=').decode()
    state = secrets.token_urlsafe(16)

    # Method 1: Try accounts.oauth.authorize API
    print("\nMethod 1: accounts.oauth.authorize")
    try:
        result = make_request(
            f"{GIGYA_API_URL}/accounts.oauth.authorize",
            {
                "apiKey": GIGYA_API_KEY,
                "login_token": session_token,
                "client_id": OAUTH_CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": "openid profile email offline_access",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "format": "json"
            }
        )
        print(f"  Result: {result}")
        if result.get('errorCode') == 0 and result.get('code'):
            return result.get('code'), code_verifier
    except Exception as e:
        print(f"  Error: {e}")

    # Method 2: Try fidm.oidc.op.authorize
    print("\nMethod 2: fidm.oidc.op.authorize")
    try:
        result = make_request(
            f"{GIGYA_API_URL}/fidm.oidc.op.authorize",
            {
                "apiKey": GIGYA_API_KEY,
                "login_token": session_token,
                "client_id": OAUTH_CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": "openid profile email offline_access",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "format": "json"
            }
        )
        print(f"  Result: {result}")
        if result.get('errorCode') == 0 and result.get('code'):
            return result.get('code'), code_verifier
    except Exception as e:
        print(f"  Error: {e}")

    # Method 3: Try oidc/op/authorize endpoint directly
    print("\nMethod 3: Direct OIDC authorize endpoint")
    try:
        params = {
            "client_id": OAUTH_CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "openid profile email offline_access",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "login_token": session_token,
        }
        url = f"{OIDC_ISSUER}/authorize?{urllib.parse.urlencode(params)}"

        # Follow redirects manually
        req = urllib.request.Request(url)
        req.add_header('Cookie', f'glt_{GIGYA_API_KEY}={session_token}')

        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
        resp = opener.open(req, timeout=10)

        final_url = str(resp.url)
        print(f"  Final URL: {final_url[:100]}...")

        # Check if we got a code
        if 'code=' in final_url:
            code = urllib.parse.parse_qs(urllib.parse.urlparse(final_url).query).get('code', [None])[0]
            if code:
                return code, code_verifier
    except urllib.error.HTTPError as e:
        # Check if the redirect URL has the code
        if e.code in (301, 302, 303, 307, 308):
            location = e.headers.get('Location', '')
            print(f"  Redirect to: {location[:100]}...")
            if 'code=' in location:
                code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get('code', [None])[0]
                if code:
                    return code, code_verifier
        print(f"  HTTP Error: {e.code}")
    except Exception as e:
        print(f"  Error: {e}")

    return None, code_verifier


def try_get_tokens_alternative(session_token, uid, uid_sig, sig_ts):
    """Try alternative methods to get OIDC tokens."""
    print(f"\n=== Step 4: Try alternative token methods ===")

    # Method 1: fidm.oidc.op.getToken with session
    print("\nMethod 1: fidm.oidc.op.getToken")
    try:
        result = make_request(
            f"{GIGYA_API_URL}/fidm.oidc.op.getToken",
            {
                "apiKey": GIGYA_API_KEY,
                "login_token": session_token,
                "client_id": OAUTH_CLIENT_ID,
                "grant_type": "none",
                "response_type": "id_token token",
                "scope": "openid profile email",
                "format": "json"
            }
        )
        print(f"  errorCode: {result.get('errorCode')}, keys: {list(result.keys())}")
        if result.get('errorCode') == 0 and result.get('access_token'):
            print(f"  SUCCESS! Got access_token and id_token")
            return result
    except Exception as e:
        print(f"  Error: {e}")

    # Method 2: Try getJWT with specific fields
    print("\nMethod 2: accounts.getJWT with fields")
    try:
        result = make_request(
            f"{GIGYA_API_URL}/accounts.getJWT",
            {
                "apiKey": GIGYA_API_KEY,
                "login_token": session_token,
                "targetEnv": OAUTH_CLIENT_ID,
                "fields": "email,profile",
                "format": "json"
            }
        )
        print(f"  errorCode: {result.get('errorCode')}")
        if result.get('id_token'):
            # Decode and check the token
            token = result['id_token']
            parts = token.split('.')
            if len(parts) >= 2:
                payload = json.loads(base64.urlsafe_b64decode(parts[1] + '==').decode())
                print(f"  Token aud: {payload.get('aud')}")
                print(f"  Token iss: {payload.get('iss')}")
    except Exception as e:
        print(f"  Error: {e}")

    # Method 3: Try socialize.getToken
    print("\nMethod 3: socialize.getToken")
    try:
        result = make_request(
            f"{GIGYA_API_URL}/socialize.getToken",
            {
                "apiKey": GIGYA_API_KEY,
                "login_token": session_token,
                "format": "json"
            }
        )
        print(f"  errorCode: {result.get('errorCode')}, keys: {list(result.keys())}")
    except Exception as e:
        print(f"  Error: {e}")

    return None


def exchange_code_for_tokens(code, code_verifier):
    """Exchange authorization code for tokens."""
    print(f"\n=== Step 5: Exchange code for tokens ===")

    data = urllib.parse.urlencode({
        "client_id": OAUTH_CLIENT_ID,
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }).encode()

    req = urllib.request.Request(f"{OIDC_ISSUER}/token", data=data)
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')

    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read().decode())
        print(f"Token exchange SUCCESS!")
        print(f"  access_token: {result.get('access_token', '')[:50]}...")
        print(f"  id_token: {result.get('id_token', '')[:50]}...")
        print(f"  refresh_token: {result.get('refresh_token', '')[:50] if result.get('refresh_token') else 'None'}...")

        # Check id_token audience
        if result.get('id_token'):
            parts = result['id_token'].split('.')
            if len(parts) >= 2:
                payload = json.loads(base64.urlsafe_b64decode(parts[1] + '==').decode())
                print(f"  id_token aud: {payload.get('aud')}")

        return result
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Token exchange FAILED: {e.code}")
        print(f"  Error: {body}")
    except Exception as e:
        print(f"Token exchange error: {e}")

    return None


def main():
    import sys
    import os

    # Usage:
    #   python3 test_automated_auth.py email@example.com          # Request OTP
    #   python3 test_automated_auth.py email@example.com 123456   # Verify OTP

    email = sys.argv[1] if len(sys.argv) > 1 else "renaud@allard.it"
    otp_code = sys.argv[2] if len(sys.argv) > 2 else None

    vtoken_file = "/tmp/philips_vtoken.txt"

    if otp_code:
        # Step 2: Verify existing OTP
        if not os.path.exists(vtoken_file):
            print("Error: No vToken found. Run without OTP code first to request one.")
            return
        with open(vtoken_file, 'r') as f:
            vtoken = f.read().strip()
        print(f"Using saved vToken: {vtoken[:50]}...")
        code = otp_code
    else:
        # Step 1: Request new OTP
        vtoken = request_otp(email)
        with open(vtoken_file, 'w') as f:
            f.write(vtoken)
        print(f"\nvToken saved. Now run again with the OTP code:")
        print(f"  python3 test_automated_auth.py {email} <OTP_CODE>")
        return

    # Step 3: Verify OTP and get session
    creds = verify_otp(email, code, vtoken)

    # Step 4: Try to get OIDC authorization code
    auth_code, code_verifier = try_oidc_authorize(creds['session_token'])

    if auth_code:
        # Step 5: Exchange code for tokens
        tokens = exchange_code_for_tokens(auth_code, code_verifier)
        if tokens:
            print("\n=== SUCCESS! ===")
            print("Got valid OIDC tokens that can be used with SAS API")
            # Clean up vtoken file
            os.remove(vtoken_file) if os.path.exists(vtoken_file) else None
            return

    # Step 5 (alt): Try alternative methods
    alt_tokens = try_get_tokens_alternative(
        creds['session_token'],
        creds['uid'],
        creds['uid_signature'],
        creds['signature_timestamp']
    )

    if alt_tokens:
        print("\n=== Partial success ===")
        print("Got tokens via alternative method")
    else:
        print("\n=== FAILED ===")
        print("Could not obtain valid OIDC tokens automatically.")
        print("The browser OAuth flow with user consent is required.")


if __name__ == "__main__":
    main()
