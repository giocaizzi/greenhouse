"""HTTP transport shared by every endpoint mixin: request sending, error mapping and body decoding."""

import json
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, TypedDict, Unpack, cast

import httpx

JSONObject = dict[str, Any]


def drop_none(fields: Mapping[str, Any]) -> JSONObject:
    """Omit unset (``None``) fields so the server applies its default or keeps the current value; order is kept."""
    return {k: v for k, v in fields.items() if v is not None}


# FastAPI prefixes each validation error's ``loc`` with where the value came from.
_LOC_SOURCES = frozenset({"body", "query", "path", "header", "cookie"})


def _format_detail(detail: object) -> str:
    """Render an error ``detail`` for humans; a 422 validation list becomes ``field: message; …``.

    FastAPI answers a validation failure with a list of ``{"loc": [...], "msg": ...}``
    objects; printing that list raw showed a Python repr. Other non-string details
    are rendered as JSON.
    """
    if isinstance(detail, str):
        return detail
    if detail in ([], {}, None):
        return "The server rejected the request without details"
    if isinstance(detail, list) and all(isinstance(item, dict) and "msg" in item for item in detail):
        return "; ".join(_format_validation_item(item) for item in detail)
    return json.dumps(detail, ensure_ascii=False)


def _format_validation_item(item: Mapping[str, Any]) -> str:
    """One validation error as ``field.path: message`` (the request-part prefix dropped)."""
    loc = [str(part) for part in item.get("loc") or []]
    if loc and loc[0] in _LOC_SOURCES:
        loc = loc[1:]
    field = ".".join(loc)
    return f"{field}: {item['msg']}" if field else str(item["msg"])


def is_csv(resp: httpx.Response) -> bool:
    """Whether the server answered with a CSV document (the stats export)."""
    content_type: str = resp.headers.get("content-type", "")
    return content_type.startswith("text/csv")


def _decode_json(resp: httpx.Response) -> JSONObject | list[Any]:
    """The JSON body of a successful response; an empty or non-JSON body is a :class:`ServerError`."""
    if not resp.content.strip():
        raise ServerError(resp.status_code, "Empty response from server")
    try:
        data: JSONObject | list[Any] = resp.json()
    except ValueError:
        raise ServerError(resp.status_code, f"Invalid JSON response from server: {resp.text[:200]!r}") from None
    return data


class RequestOptions(TypedDict, total=False):
    """The httpx request settings the endpoint helpers use: query ``params`` and a JSON body."""

    params: Mapping[str, Any]
    json: object


class ServerError(Exception):
    """Raised when the server returns an error response."""

    def __init__(self, status_code: int, detail: str) -> None:
        """Keep the HTTP status (0 for transport failures) and the server's detail text."""
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Server error {status_code}: {detail}")


class Transport:
    """Send requests on ``http`` (set by ``IrrigationClient.__init__``) and decode the answers."""

    http: httpx.Client

    def _send(self, method: str, path: str, **kwargs: Unpack[RequestOptions]) -> httpx.Response:
        """Send one request; HTTP >= 400 and transport failures raise :class:`ServerError`.

        A refused connection, a timeout or any other transport error is status 0
        (B-17); the CLI prints the error and exits 1, the TUI toasts it.
        """
        try:
            resp = self.http.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise ServerError(0, f"Cannot connect to server: {e}") from None
        except httpx.TimeoutException as e:
            raise ServerError(0, f"Server did not respond in time: {e}") from None
        except httpx.TransportError as e:
            raise ServerError(0, f"Request to server failed: {e}") from None

        if resp.status_code >= HTTPStatus.BAD_REQUEST:
            try:
                detail = resp.json().get("detail", resp.text)
            except (ValueError, AttributeError):  # not JSON, or JSON that is not an object
                detail = resp.text
            raise ServerError(resp.status_code, _format_detail(detail))
        return resp

    def _request(self, method: str, path: str, **kwargs: Unpack[RequestOptions]) -> JSONObject | list[Any]:
        """Send one request and decode it; a CSV body comes back as ``{"csv": text}``.

        Besides :meth:`_send`'s errors, a 2xx body that is empty or not JSON is a
        :class:`ServerError` (B-17).
        """
        resp = self._send(method, path, **kwargs)
        if is_csv(resp):
            return {"csv": resp.text}
        return _decode_json(resp)

    def _object(self, method: str, path: str, **kwargs: Unpack[RequestOptions]) -> JSONObject:
        """Send a request whose endpoint answers with a JSON object."""
        return cast("JSONObject", self._request(method, path, **kwargs))

    def _array(self, method: str, path: str, **kwargs: Unpack[RequestOptions]) -> list[JSONObject]:
        """Send a request whose endpoint answers with a JSON array of objects."""
        return cast("list[JSONObject]", self._request(method, path, **kwargs))
