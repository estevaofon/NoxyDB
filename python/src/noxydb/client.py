from __future__ import annotations

import http.client
import json
import math
import re
import socket
import urllib.error
import urllib.request
from types import TracebackType
from typing import Any

from .errors import NoxyDBConnectionError, NoxyDBServerError, NoxyDBValidationError
from .models import LookupResult, PutResult

_DATABASE_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_MIN_INT64 = -(2**63)
_MAX_INT64 = 2**63 - 1


def _validate_string(value: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise NoxyDBValidationError("string contains an isolated surrogate") from error


def _validate_json_value(value: object, active: set[int]) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        _validate_string(value)
        return
    if isinstance(value, int):
        if value < _MIN_INT64 or value > _MAX_INT64:
            raise NoxyDBValidationError("integer is outside signed 64-bit range")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NoxyDBValidationError("float must be finite")
        return
    if isinstance(value, (list, dict)):
        identity = id(value)
        if identity in active:
            raise NoxyDBValidationError("circular document")
        active.add(identity)
        try:
            if isinstance(value, list):
                for item in value:
                    _validate_json_value(item, active)
            else:
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise NoxyDBValidationError("document keys must be strings")
                    _validate_string(key)
                    _validate_json_value(item, active)
        finally:
            active.remove(identity)
        return
    raise NoxyDBValidationError("document is not JSON-compatible")


def _require_bool(response: dict[str, Any], field: str) -> bool:
    value = response.get(field)
    if not isinstance(value, bool):
        raise NoxyDBConnectionError(f"invalid server response: {field}")
    return value


def _require_string(response: dict[str, Any], field: str) -> str:
    value = response.get(field)
    if not isinstance(value, str):
        raise NoxyDBConnectionError(f"invalid server response: {field}")
    return value


def _require_empty_error(response: dict[str, Any]) -> None:
    if _require_string(response, "error") != "":
        raise NoxyDBConnectionError("invalid server response: error")


def _require_operation_result(response: dict[str, Any]) -> tuple[bool, str]:
    success = _require_bool(response, "success")
    error = _require_string(response, "error")
    if success == (error != ""):
        raise NoxyDBConnectionError("invalid server response: error")
    return success, error


def _reject_json_constant(value: str) -> object:
    raise json.JSONDecodeError("invalid JSON constant", value, 0)


def _declared_content_type(error: urllib.error.HTTPError) -> str:
    """Content-Type declarado na resposta de erro, normalizado.

    Devolve "" quando o cabecalho esta ausente. `get_content_type()` sozinho
    nao serve para isso: sem cabecalho ele assume o padrao MIME `text/plain`,
    e uma resposta sem Content-Type nao e um erro de transporte.
    """
    headers = getattr(error, "headers", None)
    if headers is None or headers.get("Content-Type") is None:
        return ""
    return headers.get_content_type()


class NoxyDBClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765", timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> bool:
        response = self._request("/v1/health", method="GET")
        success = _require_bool(response, "success")
        status = _require_string(response, "status")
        if not success or status != "ok":
            raise NoxyDBConnectionError("invalid server response")
        return True

    def open_database(self, name: str) -> "Database":
        if not isinstance(name, str) or _DATABASE_NAME.fullmatch(name) is None:
            raise NoxyDBValidationError("invalid database name")
        response = self._request("/v1/open", {"database": name})
        success, error = _require_operation_result(response)
        if not success:
            raise NoxyDBServerError(200, error)
        return Database(self, name)

    def shutdown(self) -> None:
        """Pede o encerramento do servidor.

        So funciona contra um servidor iniciado com --enable-shutdown; sem a
        flag a rota nao existe e o servidor responde 404.
        """
        response = self._request("/v1/shutdown", {})
        success, error = _require_operation_result(response)
        if not success:
            raise NoxyDBServerError(200, error)

    def _request(
        self,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        method: str = "POST",
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            try:
                raw_error = error.read()
            except (
                http.client.IncompleteRead,
                TimeoutError,
                socket.timeout,
                OSError,
            ) as read_error:
                raise NoxyDBConnectionError("invalid error response") from read_error
            # Spec SS6.4: a classificacao vem do Content-Type, nunca do codigo
            # sozinho. Erros da camada de transporte vem do http_server da
            # stdlib como text/plain com a razao no corpo, e podem usar
            # qualquer status -- inclusive 400, que a camada de transporte
            # tambem emite (request line invalida, Content-Length duplicado,
            # caracteres de controle em cabecalho). Uma lista fixa de codigos
            # classificava esses 400 como falha de conexao e perdia o status.
            if _declared_content_type(error) == "text/plain":
                reason = raw_error.decode("utf-8", errors="replace").strip()
                if not reason:
                    raise NoxyDBConnectionError("invalid error response") from error
                raise NoxyDBServerError(error.code, reason) from error
            # application/json (ou sem Content-Type): e para ser o envelope
            # {success, error} da API. Corpo que nao decodifica como JSON valido
            # nesse canal e resposta corrompida, nao erro de transporte.
            try:
                decoded_error = json.loads(
                    raw_error.decode("utf-8"),
                    parse_constant=_reject_json_constant,
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise NoxyDBConnectionError("invalid error response") from error
            if (
                not isinstance(decoded_error, dict)
                or decoded_error.get("success") is not False
                or not isinstance(decoded_error.get("error"), str)
                or decoded_error["error"] == ""
            ):
                raise NoxyDBConnectionError("invalid error response") from error
            raise NoxyDBServerError(error.code, decoded_error["error"]) from error
        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            OSError,
            http.client.IncompleteRead,
        ) as error:
            raise NoxyDBConnectionError("failed to connect to NoxyDB server") from error
        try:
            decoded = json.loads(
                raw.decode("utf-8"),
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NoxyDBConnectionError("invalid JSON response") from error
        if not isinstance(decoded, dict):
            raise NoxyDBConnectionError("invalid server response")
        return decoded


class Database:
    def __init__(self, client: NoxyDBClient, name: str) -> None:
        self._client = client
        self.name = name
        self._open = True

    def _ensure_open(self) -> None:
        if not self._open:
            raise NoxyDBValidationError("database handle is closed")

    def _require_key(self, key: str) -> None:
        if not isinstance(key, str):
            raise NoxyDBValidationError("key must be a string")
        _validate_string(key)

    def put(self, key: str, value: dict[str, object]) -> PutResult:
        self._ensure_open()
        self._require_key(key)
        if not isinstance(value, dict):
            raise NoxyDBValidationError("document root must be an object")
        _validate_json_value(value, set())
        response = self._client._request(
            "/v1/put", {"database": self.name, "key": key, "value": value}
        )
        success, error = _require_operation_result(response)
        return PutResult(success, error)

    def get(self, key: str) -> LookupResult:
        self._ensure_open()
        self._require_key(key)
        response = self._client._request("/v1/get", {"database": self.name, "key": key})
        found = _require_bool(response, "found")
        value = response.get("value")
        if not isinstance(value, dict):
            raise NoxyDBConnectionError("invalid server response: value")
        if not found and value != {}:
            raise NoxyDBConnectionError("invalid server response: value")
        try:
            _validate_json_value(value, set())
        except NoxyDBValidationError as error:
            raise NoxyDBConnectionError("invalid server response: value") from error
        _require_empty_error(response)
        return LookupResult(found, value)

    def exists(self, key: str) -> bool:
        self._ensure_open()
        self._require_key(key)
        response = self._client._request("/v1/exists", {"database": self.name, "key": key})
        exists = _require_bool(response, "exists")
        _require_empty_error(response)
        return exists

    def remove(self, key: str) -> None:
        self._ensure_open()
        self._require_key(key)
        response = self._client._request("/v1/remove", {"database": self.name, "key": key})
        success, error = _require_operation_result(response)
        if not success:
            raise NoxyDBServerError(200, error)

    def close_database(self) -> None:
        if not self._open:
            return
        response = self._client._request("/v1/close", {"database": self.name})
        success, error = _require_operation_result(response)
        if not success:
            raise NoxyDBServerError(200, error)
        self._open = False

    def close(self) -> None:
        self.close_database()

    def __enter__(self) -> "Database":
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close_database()
