from .rest_client import RouterOSRestClient
from .api_client import RouterOSApiClient
from ..security import SecretBox
from ..settings import settings
from ..models import Router


def make_client(router: Router):
    box = SecretBox(settings.secret_key)
    password = box.decrypt(router.secret_enc) or ""
    if router.proto in ("rest", "rest-http"):
        # rest-http forces http; rest prefers https
        https = False if router.proto == "rest-http" else True
        return RouterOSRestClient(
            host=router.host,
            port=router.port,
            username=router.username,
            password=password,
            tls_verify=router.tls_verify,
            https=https,
        )
    else:
        # api-plain forces no TLS; api uses TLS
        use_tls = False if router.proto == "api-plain" else True
        return RouterOSApiClient(
            host=router.host,
            port=router.port,
            username=router.username,
            password=password,
            use_tls=use_tls,
            ssl_verify=router.tls_verify if use_tls else False,
        )


