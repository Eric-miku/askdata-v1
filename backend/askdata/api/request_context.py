from __future__ import annotations

import contextvars


_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("askdata_request_id", default="")


def SetRequestId(request_id: str):
    return _request_id.set(request_id)


def ResetRequestId(token) -> None:
    _request_id.reset(token)


def GetRequestId() -> str:
    return _request_id.get()
