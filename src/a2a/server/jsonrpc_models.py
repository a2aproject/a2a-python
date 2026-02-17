from typing import Any, Literal

from pydantic import BaseModel


class JSONRPCBaseModel(BaseModel):
    """Base model for JSON-RPC objects."""

    model_config = {
        'extra': 'allow',
        'populate_by_name': True,
        'arbitrary_types_allowed': True,
    }


class JSONRPCError(JSONRPCBaseModel):
    """Base model for JSON-RPC error objects."""

    code: int
    message: str
    data: Any | None = None


class JSONParseError(JSONRPCError):
    """Error raised when invalid JSON was received by the server."""

    code: Literal[-32700] = -32700  # pyright: ignore [reportIncompatibleVariableOverride]
    message: str = 'Parse error'


class InvalidRequestError(JSONRPCError):
    """Error raised when the JSON sent is not a valid Request object."""

    code: Literal[-32600] = -32600  # pyright: ignore [reportIncompatibleVariableOverride]
    message: str = 'Invalid Request'


class MethodNotFoundError(JSONRPCError):
    """Error raised when the method does not exist / is not available."""

    code: Literal[-32601] = -32601  # pyright: ignore [reportIncompatibleVariableOverride]
    message: str = 'Method not found'


class InvalidParamsError(JSONRPCError):
    """Error raised when invalid method parameter(s)."""

    code: Literal[-32602] = -32602  # pyright: ignore [reportIncompatibleVariableOverride]
    message: str = 'Invalid params'


class InternalError(JSONRPCError):
    """Error raised when internal JSON-RPC error."""

    code: Literal[-32603] = -32603  # pyright: ignore [reportIncompatibleVariableOverride]
    message: str = 'Internal error'
