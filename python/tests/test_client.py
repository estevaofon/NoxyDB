import json
import math
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from noxydb import (
    Database,
    LookupResult,
    NoxyDBClient,
    NoxyDBConnectionError,
    NoxyDBError,
    NoxyDBServerError,
    NoxyDBValidationError,
    PutResult,
)


class _RecordingServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _RequestHandler)
        self.requests: list[dict[str, Any]] = []
        self.responses: list[tuple[int, object] | tuple[int, object, int]] = []


class _RequestHandler(BaseHTTPRequestHandler):
    server: _RecordingServer

    def do_GET(self) -> None:
        self._handle_request()

    def do_POST(self) -> None:
        self._handle_request()

    def _handle_request(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        body = json.loads(raw_body.decode("utf-8")) if raw_body else None
        self.server.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "body": body,
                "raw_body": raw_body,
                "headers": dict(self.headers.items()),
            }
        )
        queued_response = self.server.responses.pop(0)
        status, response = queued_response[:2]
        if isinstance(response, bytes):
            encoded = response
        else:
            encoded = json.dumps(
                response,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        content_length = queued_response[2] if len(queued_response) == 3 else len(encoded)
        self.send_header("Content-Length", str(content_length))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        pass


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = _RecordingServer()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.client = NoxyDBClient(f"http://{host}:{port}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        self.server.requests.clear()
        self.server.responses.clear()

    def _open_database(self) -> Database:
        self.server.responses.append((200, {"success": True, "error": ""}))
        return self.client.open_database("usuarios")

    def test_crud_mirrors_noxydb_api(self) -> None:
        self.server.responses.extend(
            [
                (200, {"success": True, "error": ""}),
                (200, {"success": True, "error": ""}),
                (200, {"found": True, "value": {"name": "Estevão"}, "error": ""}),
                (200, {"exists": True, "error": ""}),
                (200, {"success": True, "error": ""}),
                (200, {"success": True, "error": ""}),
            ]
        )

        db = self.client.open_database("usuarios")
        self.assertIsInstance(db, Database)
        self.assertEqual(db.name, "usuarios")
        self.assertEqual(db.put("user:1", {"name": "Estevão"}), PutResult(True, ""))
        self.assertEqual(db.get("user:1"), LookupResult(True, {"name": "Estevão"}))
        self.assertTrue(db.exists("user:1"))
        self.assertIsNone(db.remove("user:1"))
        self.assertIsNone(db.close())

        self.assertEqual(
            [request["path"] for request in self.server.requests],
            ["/v1/open", "/v1/put", "/v1/get", "/v1/exists", "/v1/remove", "/v1/close"],
        )
        self.assertEqual(
            [request["body"] for request in self.server.requests],
            [
                {"database": "usuarios"},
                {"database": "usuarios", "key": "user:1", "value": {"name": "Estevão"}},
                {"database": "usuarios", "key": "user:1"},
                {"database": "usuarios", "key": "user:1"},
                {"database": "usuarios", "key": "user:1"},
                {"database": "usuarios"},
            ],
        )

    def test_requests_use_utf8_json_and_accurate_content_length(self) -> None:
        self.server.responses.extend(
            [
                (200, {"success": True, "error": ""}),
                (200, {"success": True, "error": ""}),
            ]
        )
        db = self.client.open_database("usuarios")

        db.put("chave:á", {"saudação": "Olá, 世界"})

        request = self.server.requests[-1]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["headers"]["Content-Type"], "application/json")
        self.assertEqual(request["headers"]["Accept"], "application/json")
        self.assertEqual(int(request["headers"]["Content-Length"]), len(request["raw_body"]))
        self.assertIn("Olá, 世界".encode("utf-8"), request["raw_body"])
        self.assertNotIn(b"\\u", request["raw_body"])

    def test_health_uses_get_without_a_body(self) -> None:
        self.server.responses.append((200, {"success": True, "status": "ok"}))

        self.assertTrue(self.client.health())

        request = self.server.requests[0]
        self.assertEqual((request["method"], request["path"], request["body"]), ("GET", "/v1/health", None))

    def test_health_requires_valid_success_and_status(self) -> None:
        self.server.responses.extend(
            [
                (200, {"success": True, "status": "starting"}),
                (200, {"success": False, "status": "ok"}),
            ]
        )

        self.assertFalse(self.client.health())
        self.assertFalse(self.client.health())

    def test_missing_lookup_returns_typed_empty_result(self) -> None:
        self.server.responses.extend(
            [
                (200, {"success": True, "error": ""}),
                (200, {"found": False, "value": {}, "error": ""}),
            ]
        )
        db = self.client.open_database("usuarios")

        result = db.get("missing")

        self.assertIsInstance(result, LookupResult)
        self.assertEqual(result, LookupResult(False, {}))

    def test_result_models_are_immutable(self) -> None:
        with self.assertRaises((AttributeError, TypeError)):
            PutResult(True, "").success = False
        with self.assertRaises((AttributeError, TypeError)):
            LookupResult(False, {}).found = True

    def test_close_is_idempotent_and_rejects_later_operations(self) -> None:
        self.server.responses.extend(
            [
                (200, {"success": True, "error": ""}),
                (200, {"success": True, "error": ""}),
            ]
        )
        db = self.client.open_database("usuarios")

        self.assertIsNone(db.close_database())
        self.assertIsNone(db.close_database())
        with self.assertRaisesRegex(NoxyDBValidationError, "database handle is closed"):
            db.get("user:1")

        self.assertEqual([request["path"] for request in self.server.requests], ["/v1/open", "/v1/close"])

    def test_database_context_manager_closes_handle(self) -> None:
        self.server.responses.extend(
            [
                (200, {"success": True, "error": ""}),
                (200, {"exists": True, "error": ""}),
                (200, {"success": True, "error": ""}),
            ]
        )

        with self.client.open_database("usuarios") as db:
            self.assertTrue(db.exists("key"))

        with self.assertRaisesRegex(NoxyDBValidationError, "database handle is closed"):
            db.exists("key")

    def test_database_name_is_validated_locally(self) -> None:
        invalid_names: list[object] = ["", "with space", "a" * 65, "acentuação", None, 42]
        for name in invalid_names:
            with self.subTest(name=name):
                with self.assertRaisesRegex(NoxyDBValidationError, "invalid database name"):
                    self.client.open_database(name)  # type: ignore[arg-type]
        self.assertEqual(self.server.requests, [])

    def test_keys_must_be_strings(self) -> None:
        db = self._open_database()

        for operation in (db.put, db.get, db.exists, db.remove):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(NoxyDBValidationError, "key must be a string"):
                    if operation == db.put:
                        operation(7, {})  # type: ignore[arg-type]
                    else:
                        operation(7)  # type: ignore[arg-type]

        self.assertEqual(len(self.server.requests), 1)

    def test_rejects_non_object_document_roots(self) -> None:
        db = self._open_database()

        for value in (None, [], "document", 1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(NoxyDBValidationError, "document root must be an object"):
                    db.put("key", value)  # type: ignore[arg-type]

    def test_rejects_non_string_document_keys(self) -> None:
        db = self._open_database()

        with self.assertRaisesRegex(NoxyDBValidationError, "document keys must be strings"):
            db.put("key", {1: "value"})  # type: ignore[dict-item]

    def test_rejects_cycles_but_accepts_shared_containers(self) -> None:
        self.server.responses.extend(
            [
                (200, {"success": True, "error": ""}),
                (200, {"success": True, "error": ""}),
            ]
        )
        db = self.client.open_database("usuarios")
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic

        with self.assertRaisesRegex(NoxyDBValidationError, "circular document"):
            db.put("cycle", cyclic)

        shared = [1, 2, 3]
        self.assertEqual(db.put("shared", {"left": shared, "right": shared}), PutResult(True, ""))

    def test_rejects_non_finite_floats_and_non_json_values(self) -> None:
        db = self._open_database()

        invalid = [math.nan, math.inf, -math.inf, b"bytes", object()]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(NoxyDBValidationError):
                    db.put("key", {"value": value})

    def test_enforces_signed_64_bit_integer_boundaries(self) -> None:
        self.server.responses.extend(
            [
                (200, {"success": True, "error": ""}),
                (200, {"success": True, "error": ""}),
                (200, {"success": True, "error": ""}),
            ]
        )
        db = self.client.open_database("usuarios")

        self.assertEqual(db.put("min", {"value": -(2**63)}), PutResult(True, ""))
        self.assertEqual(db.put("max", {"value": 2**63 - 1}), PutResult(True, ""))
        for value in (-(2**63) - 1, 2**63):
            with self.subTest(value=value):
                with self.assertRaisesRegex(NoxyDBValidationError, "signed 64-bit range"):
                    db.put("invalid", {"value": value})

    def test_http_server_error_exposes_status_and_message(self) -> None:
        self.server.responses.append((409, {"success": False, "error": "database is not open"}))

        with self.assertRaises(NoxyDBServerError) as raised:
            self.client._request("/v1/get", {"database": "usuarios", "key": "k"})

        self.assertIsInstance(raised.exception, NoxyDBError)
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(str(raised.exception), "database is not open")

    def test_success_status_with_false_success_raises_server_error(self) -> None:
        for operation, response in (
            ("open", {"success": False, "error": "open failed"}),
            ("remove", {"success": False, "error": "remove failed"}),
            ("close", {"success": False, "error": "close failed"}),
        ):
            with self.subTest(operation=operation):
                self.server.requests.clear()
                self.server.responses.clear()
                if operation == "open":
                    self.server.responses.append((200, response))
                    call = lambda: self.client.open_database("usuarios")
                else:
                    db = self._open_database()
                    self.server.responses.append((200, response))
                    call = db.remove if operation == "remove" else db.close
                    if operation == "remove":
                        call = lambda: db.remove("key")
                with self.assertRaises(NoxyDBServerError) as raised:
                    call()
                self.assertEqual(raised.exception.status, 200)
                self.assertEqual(str(raised.exception), f"{operation} failed")

    def test_malformed_success_responses_raise_connection_error(self) -> None:
        malformed_responses = [
            b"not-json",
            ["not", "an", "object"],
            {"success": "yes", "error": ""},
            {"success": True, "error": 5},
        ]
        for response in malformed_responses:
            with self.subTest(response=response):
                self.server.requests.clear()
                self.server.responses.clear()
                self.server.responses.append((200, response))
                if response in (b"not-json", ["not", "an", "object"]):
                    call = self.client.open_database
                else:
                    db = Database(self.client, "usuarios")
                    self.server.responses[0] = (200, response)
                    call = lambda _name: db.put("key", {})
                with self.assertRaises(NoxyDBConnectionError):
                    call("usuarios")

    def test_malformed_lookup_value_raises_connection_error(self) -> None:
        self.server.responses.extend(
            [
                (200, {"success": True, "error": ""}),
                (200, {"found": False, "value": None, "error": ""}),
            ]
        )
        db = self.client.open_database("usuarios")

        with self.assertRaisesRegex(NoxyDBConnectionError, "invalid server response: value"):
            db.get("missing")

    def test_malformed_error_response_raises_connection_error(self) -> None:
        for response in (b"not-json", {"success": False}, ["not", "an", "object"]):
            with self.subTest(response=response):
                self.server.responses.clear()
                self.server.responses.append((500, response))
                with self.assertRaisesRegex(NoxyDBConnectionError, "invalid error response"):
                    self.client._request("/v1/open", {"database": "usuarios"})

    def test_connection_failure_is_wrapped(self) -> None:
        unavailable = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        host, port = unavailable.server_address
        unavailable.server_close()
        client = NoxyDBClient(f"http://{host}:{port}", timeout=0.2)

        with self.assertRaisesRegex(NoxyDBConnectionError, "failed to connect"):
            client.health()

    def test_truncated_responses_are_wrapped_as_connection_errors(self) -> None:
        for status, response in (
            (200, b'{"success":true,"status":"ok"}'),
            (500, b'{"success":false,"error":"failed"}'),
        ):
            with self.subTest(status=status):
                self.server.responses.clear()
                self.server.responses.append((status, response, len(response) + 10))
                try:
                    self.client.health()
                except Exception as error:
                    self.assertIsInstance(error, NoxyDBConnectionError)
                else:
                    self.fail("truncated response did not raise an exception")

    def test_non_json_numeric_constants_in_responses_are_rejected(self) -> None:
        for status in (200, 500):
            for constant in (b"NaN", b"Infinity", b"-Infinity"):
                with self.subTest(status=status, constant=constant):
                    if status == 200:
                        prefix = b'{"success":true,"status":"ok","detail":'
                    else:
                        prefix = b'{"success":false,"error":"failed","detail":'
                    response = prefix + constant + b"}"
                    self.server.responses.clear()
                    self.server.responses.append((status, response))
                    try:
                        self.client.health()
                    except Exception as error:
                        self.assertIsInstance(error, NoxyDBConnectionError)
                    else:
                        self.fail(f"server response accepted non-JSON constant {constant!r}")


if __name__ == "__main__":
    unittest.main()
