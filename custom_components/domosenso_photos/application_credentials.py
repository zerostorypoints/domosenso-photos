"""Adobe IMS as the OAuth2 server for Lightroom entries; the household brings its own Adobe project."""
from homeassistant.components.application_credentials import AuthorizationServer

AUTHORIZE_URL = 'https://ims-na1.adobelogin.com/ims/authorize/v2'
TOKEN_URL = 'https://ims-na1.adobelogin.com/ims/token/v3'
SCOPES = 'openid,AdobeID,lr_partner_apis,lr_partner_rendition_apis,offline_access'
REDIRECT_URL = 'https://my.home-assistant.io/redirect/oauth'
CONSOLE_URL = 'https://developer.adobe.com/console'


async def async_get_authorization_server(hass) -> AuthorizationServer:
    # Read at call time so the smoke can point both at its fake IMS.
    return AuthorizationServer(authorize_url=AUTHORIZE_URL, token_url=TOKEN_URL)


async def async_get_description_placeholders(hass) -> dict[str, str]:
    # hassfest refuses URLs inside translation strings; they come in as placeholders.
    return {'redirect_url': REDIRECT_URL, 'console_url': CONSOLE_URL}
