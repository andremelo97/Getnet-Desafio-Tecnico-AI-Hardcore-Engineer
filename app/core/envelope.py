"""The response envelope shared by every endpoint: `{data, meta: {code, message}}`.

Success and error responses have the same shape, so a client parses one structure and
switches on `meta.code`. Errors carry `data: null`.
"""

from pydantic import BaseModel

from app.core.messages import Code, message_for


class Meta(BaseModel):
    code: Code
    message: str


class Envelope[T](BaseModel):
    data: T | None = None
    meta: Meta


def ok[T](data: T, code: Code) -> Envelope[T]:
    return Envelope[T](data=data, meta=Meta(code=code, message=message_for(code)))


def error_body(code: Code, message: str | None = None) -> dict[str, object]:
    meta = Meta(code=code, message=message or message_for(code))
    return {"data": None, "meta": meta.model_dump(mode="json")}
