# Copyright (c) 2025, Renaud Allard <renaud@allard.it>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
"""Constants for the Philips HomeID integration."""

DOMAIN = "philips_homeid"

# Cloud API Endpoints
API_BASE_URL = "https://www.backend.vbs.versuni.com/api"
IOT_API_URL = "https://air.acc.eu-da.iot.versuni.com/api"

# Gigya CDC API (for OTP authentication - fallback)
GIGYA_API_KEY = "4_JGZWlP8eQHpEqkvQElolbA"
GIGYA_API_URL = "https://cdc.accounts.home.id"

# OIDC Configuration (for Gigya CDC)
OIDC_ISSUER = f"https://cdc.accounts.home.id/oidc/op/v1.0/{GIGYA_API_KEY}"
OIDC_DEVICE_AUTH_ENDPOINT = f"{OIDC_ISSUER}/device_authorization"
OIDC_TOKEN_ENDPOINT = f"{OIDC_ISSUER}/token"
OIDC_AUTH_ENDPOINT = f"{OIDC_ISSUER}/authorize"
OAUTH_CLIENT_ID = "-u6aTznrxp9_9e_0a57CpvEG"

# HSDP IAM Configuration (from app decompilation - BackendConfigKt)
HSDP_IAM_URL = "https://iam-service.eu-west.philips-healthsuite.com"
HSDP_CLIENT_ID = "21e431131cb04a0eb56"
HSDP_CLIENT_SECRET = "@@3f2.6lo21_2F61"
HSDP_TOKEN_ENDPOINT = f"{HSDP_IAM_URL}/authorize/oauth2/token"

# Mobile app redirect URI for Gigya CDC OAuth (must match the client_id used)
# For OAUTH_CLIENT_ID (-u6aTznrxp9_9e_0a57CpvEG), use the oneka app redirect
MOBILE_APP_REDIRECT_URI = "com.philips.ka.oneka.app.prod://oauthredirect"

# SAS Token Exchange URLs (from app decompilation)
# Primary: HomeID API
HOMEID_API_URL = "https://www.home.id/api"
FALLBACK_TOKEN_EXCHANGE_URL = f"{HOMEID_API_URL}/sas/hsdp-token"
# Alternative: Backend VBS
ALT_TOKEN_EXCHANGE_URL = f"{API_BASE_URL}/sls/hsdp/token"

# Local discovery configuration keys
CONF_CPP_ID = "cpp_id"
CONF_DEVICE_ID = "device_id"
CONF_MODEL = "model"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"

# Cloud authentication configuration keys
CONF_UID = "uid"
CONF_UID_SIGNATURE = "uid_signature"
CONF_SIGNATURE_TIMESTAMP = "signature_timestamp"

# Local discovery constants
ZEROCONF_TYPE = "_philipscondor._tcp.local."
SSDP_ST = "urn:philips-com:device:DiProduct:1"

# Default values
DEFAULT_SCAN_INTERVAL = 30
