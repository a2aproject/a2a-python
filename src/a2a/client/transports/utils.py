from typing import Any

from a2a.client.middleware import ClientCallContext
from a2a.extensions.common import HTTP_EXTENSION_HEADER


def get_http_args(context: ClientCallContext | None) -> dict[str, Any] | None:
    return context.state.get('http_kwargs') if context else None

def update_extension_header(
    http_kwargs: dict[str, Any], extensions: list[str] | None
) -> dict[str, Any]:
    if extensions:
        headers = http_kwargs.setdefault('headers', {})
        existing_extensions_str = headers.get(HTTP_EXTENSION_HEADER, '')

        existing_extensions_list = [
            e.strip() for e in existing_extensions_str.split(',') if e.strip()
        ]
        new_extensions = [
            ext for ext in extensions if ext not in existing_extensions_list
        ]

        headers[HTTP_EXTENSION_HEADER] = ','.join(
            existing_extensions_list + new_extensions
        )
    return http_kwargs
