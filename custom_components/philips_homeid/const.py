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

# Air+ app OAuth client. Devices paired in the standalone Philips Air+ app
# (com.philips.air) are registered against this client on the same Gigya
# account, and the DA IoT device registry only lists a device to the client
# it was paired with. Discovery mints a token with this client from the same
# OTP session to find Air+-paired purifiers (AC0650/AC0651/AC1715). The token
# is obtained with the same pure-HTTP prompt=none PKCE flow as the HomeID
# client and needs no client secret. Values from the Air+ app (issue #33).
AIRPLUS_CLIENT_ID = "-XsK7O6iEkLml77yDGDUi0ku"
AIRPLUS_REDIRECT_URI = "com.philips.air://loginredirect"
AIRPLUS_SCOPES = (
    "openid email profile address "
    "DI.Account.read DI.Account.write DI.AccountProfile.read "
    "DI.AccountProfile.write DI.AccountGeneralConsent.read "
    "DI.AccountGeneralConsent.write DI.GeneralConsent.read subscriptions "
    "profile_extended consents DI.AccountSubscription.read "
    "DI.AccountSubscription.write"
)

# OAuth client identifiers stored on a config entry so the runtime relay
# refreshes tokens with the same client the device was discovered under.
OAUTH_CLIENT_HOMEID = "homeid"
OAUTH_CLIENT_AIRPLUS = "airplus"

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
# Which OAuth client the entry's refresh token belongs to (OAUTH_CLIENT_*).
# Absent on entries created before Air+ support; treated as HomeID.
CONF_OAUTH_CLIENT = "oauth_client"
CONF_RECIPE_CACHE = "recipe_cache"
CONF_RECIPE_LANGUAGE = "recipe_language"
CONF_AUTOCOOK_CATALOG_FETCHED = "autocook_catalog_fetched"
# Custom "My Presets" created by the user in the Philips HomeID app. These
# live in the cloud account, are fetched per appliance and cached here.
CONF_MY_PRESETS = "my_presets"
CONF_MY_PRESETS_LANGUAGE = "my_presets_language"
CONF_MY_PRESETS_FETCHED = "my_presets_fetched"
# Cloud appliance id (last path segment of the appliance self link), needed
# to resolve the per-appliance My Presets endpoint.
CONF_APPLIANCE_ID = "appliance_id"

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

# Keep warm temperature the appliances default to, in Celsius. The keep warm
# number entity bounds are written in Celsius for the same reason: that is how
# the appliances are specified, and a Fahrenheit model accepts the same
# physical range.
KEEP_WARM_DEFAULT_TEMP_C = 65

# Default values
DEFAULT_SCAN_INTERVAL = 60  # 1 minute when idle
ACTIVE_SCAN_INTERVAL = 10  # 10 seconds when airfryer is cooking
FUSION_HEARTBEAT_INTERVAL = 300  # 5 minutes heartbeat for MQTT devices

# The best-effort cloud fetches (AutoCook catalog, My Presets, Rita drinks)
# are kicked from the state-update path, which runs on every push, so a cloud
# outage must not turn every push into a retry.
CLOUD_FETCH_RETRY_DELAY = 600  # 10 minutes before retrying a failed fetch

# The cloud firmware job list only changes when Versuni queues an update, and
# every check costs a token refresh, so it is read a few times a day.
OTA_JOBS_POLL_INTERVAL = 21600  # 6 hours between firmware job checks

# Key for the cloud firmware-job sensor. Not a device property: the coordinator
# announces it so the binary sensor platform knows an answer has arrived.
OTA_UPDATE_KEY = "firmware_update_available"

# FUSION / DaConnect platform defaults (from APK DomainConfig)
FUSION_PLATFORM_REST_URL = "prod.eu-da.iot.versuni.com"
FUSION_TENANT = "da"
FUSION_MQTT_HOST = "ats.prod.eu-da.iot.versuni.com"

# Rita espresso machine built-in drinks (RitaDrinkId -> name, from APK
# RitaDrinkKt). A built-in drink is brewed via REMOTE_BREW with Recipe_id set
# to the drink id. Only the hot drinks are listed; iced and cold-brew variants
# are omitted, and Hot Water (id 21) has its own dedicated button. The machine
# advertises the drinks it actually supports only through the cloud catalog, so
# a drink the model lacks is simply rejected with a control-status error.
RITA_BUILTIN_DRINKS: dict[int, str] = {
    1: "Ristretto",
    2: "Espresso",
    4: "Espresso Lungo",
    5: "Double Espresso",
    7: "Caffè Crema",
    8: "Americano",
    10: "Espresso Macchiato",
    11: "Cortado",
    13: "Mélange",
    14: "Cappuccino",
    15: "Flat White",
    16: "Caffè Latte",
    17: "Café au Lait",
    19: "Latte Macchiato",
    20: "Froth Milk",
    23: "Galão",
    24: "Italian Cappuccino",
    35: "Long Black",
    38: "Cut",
    39: "Red Eye",
    40: "Black Eye",
    41: "Dripped Eye",
    42: "Piccolo Latte",
    43: "Magic",
    54: "Babyccino",
    61: "Cappuccino XL",
}

# The Rita brew-recipe dropdown mixes per-profile custom recipes (slots 0-79)
# with the global built-in drinks above. Built-in drinks are offset past the
# recipe slot range so a single integer selection can name either without a
# collision; the brew path routes any value at or above the offset to a
# built-in-drink brew after subtracting it.
RITA_BUILTIN_DRINK_OFFSET = 1000

# Recipe slots are handed out to profiles in fixed blocks: the profile in
# Pr_Names position N owns slots N*8 through N*8+7. The app reads each recipe
# port in chunks of this size and picks the chunk by the profile's position
# (RitaRecipesPortDeserializer, ObserveActiveRitaProfileUseCase), and it pads
# every profile's recipeIdOrderList to the same count (RitaProfileMapperKt).
RITA_RECIPES_PER_PROFILE = 8
