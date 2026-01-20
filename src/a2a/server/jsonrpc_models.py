from typing import Any, Literal

from pydantic import BaseModel


class JSONRPCBaseModel(BaseModel):
    model_config = {
        'extra': 'allow',
        'populate_by_name': True,
        'arbitrary_types_allowed': True,
    }


class JSONRPCError(JSONRPCBaseModel):
    code: int
    message: str
    data: Any | None = None


class JSONParseError(JSONRPCError):
    code: Literal[-32700] = -32700
    message: str = 'Parse error'


class InvalidRequestError(JSONRPCError):
    code: Literal[-32600] = -32600
    message: str = 'Invalid Request'


class MethodNotFoundError(JSONRPCError):
    code: Literal[-32601] = -32601
    message: str = 'Method not found'


class InvalidParamsError(JSONRPCError):
    code: Literal[-32602] = -32602
    message: str = 'Invalid params'


class InternalError(JSONRPCError):
    code: Literal[-32603] = -32603
    message: str = 'Internal error'
