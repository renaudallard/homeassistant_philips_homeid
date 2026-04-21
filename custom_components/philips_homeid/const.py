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

# Gigya CDC API (for cloud OTP authentication)
GIGYA_API_KEY = "4_JGZWlP8eQHpEqkvQElolbA"
GIGYA_API_URL = "https://cdc.accounts.home.id"

# OIDC Configuration (for Gigya CDC)
OIDC_ISSUER = f"https://cdc.accounts.home.id/oidc/op/v1.0/{GIGYA_API_KEY}"
OIDC_TOKEN_ENDPOINT = f"{OIDC_ISSUER}/token"
OIDC_AUTH_ENDPOINT = f"{OIDC_ISSUER}/authorize"
OAUTH_CLIENT_ID = "-u6aTznrxp9_9e_0a57CpvEG"

# Mobile app redirect URI (must match the registered client_id)
MOBILE_APP_REDIRECT_URI = "com.philips.ka.oneka.app.prod://oauthredirect"

# Local discovery configuration keys
CONF_CPP_ID = "cpp_id"
CONF_DEVICE_ID = "device_id"
CONF_MODEL = "model"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_USE_HTTPS = "use_https"
CONF_ENCRYPTION_KEY = "encryption_key"
CONF_AIRFRYER_PORT = "airfryer_port"
CONF_CLOUD_REFRESH_TOKEN = "cloud_refresh_token"
CONF_RECIPE_CACHE = "recipe_cache"
CONF_RECIPE_LANGUAGE = "recipe_language"
CONF_AUTOCOOK_CATALOG_FETCHED = "autocook_catalog_fetched"

# FUSION (cloud MQTT relay) configuration keys
CONF_IS_FUSION = "is_fusion"
CONF_THING_NAME = "thing_name"
CONF_TENANT = "tenant"
CONF_MQTT_HOST = "mqtt_host"
CONF_PLATFORM_REST_URL = "platform_rest_url"

# Local discovery constants
ZEROCONF_TYPE = "_philipscondor._tcp.local."
SSDP_ST = "urn:philips-com:device:DiProduct:1"

# Options keys
CONF_SCAN_INTERVAL = "scan_interval"
CONF_ACTIVE_SCAN_INTERVAL = "active_scan_interval"

# Default values
DEFAULT_SCAN_INTERVAL = 60  # 1 minute when idle
ACTIVE_SCAN_INTERVAL = 10  # 10 seconds when airfryer is cooking
FUSION_HEARTBEAT_INTERVAL = 300  # 5 minutes heartbeat for MQTT devices

# FUSION / DaConnect platform defaults (from APK DomainConfig)
FUSION_PLATFORM_REST_URL = "prod.eu-da.iot.versuni.com"
FUSION_TENANT = "da"
FUSION_MQTT_HOST = "ats.prod.eu-da.iot.versuni.com"
