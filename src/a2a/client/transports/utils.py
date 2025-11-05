from typing import Any

from google.protobuf import struct_pb2

from a2a.client.middleware import ClientCallContext
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.utils import proto_utils


def get_http_args(context: ClientCallContext | None) -> dict[str, Any] | None:
    return context.state.get('http_kwargs') if context else None


def __merge_extensions(
    existing_extensions: str, new_extensions: list[str]
) -> str:
    existing_extensions_list = [
        e.strip() for e in existing_extensions.split(',') if e.strip()
    ]
    new_extensions = [
        ext for ext in new_extensions if ext not in existing_extensions_list
    ]
    return ','.join(existing_extensions_list + new_extensions)


def update_extension_header(
    http_kwargs: dict[str, Any], extensions: list[str] | None
) -> dict[str, Any]:
    if extensions:
        headers = http_kwargs.setdefault('headers', {})
        existing_extensions_str = headers.get(HTTP_EXTENSION_HEADER, '')

        headers[HTTP_EXTENSION_HEADER] = __merge_extensions(
            existing_extensions_str, extensions
        )
    return http_kwargs


def update_extension_metadata(
    metadata: dict[str, Any] | None, extensions: list[str] | None
) -> struct_pb2.Struct | None:
    if metadata is None:
        metadata = {}
    if extensions:
        existing_extensions_str = str(metadata.get(HTTP_EXTENSION_HEADER, ''))
        metadata[HTTP_EXTENSION_HEADER] = __merge_extensions(
            existing_extensions_str, extensions
        )
    return proto_utils.ToProto.metadata(metadata if metadata else None)
