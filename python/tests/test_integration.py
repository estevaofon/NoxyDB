import os
import re
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from noxydb import NoxyDBClient, NoxyDBServerError, PutResult


class _ServerHarness(unittest.TestCase):
    """Sobe/derruba um noxydb_server.nx real; sem metodos de teste.

    Extraida de IntegrationTests porque ShutdownRouteTests precisa do mesmo
    harness sob --enable-shutdown sem herdar (e reexecutar sob a flag) os
    testes de IntegrationTests -- em particular
    test_shutdown_route_is_absent_without_the_flag, que pressupoe a rota
    ausente e portanto contradiz uma classe que liga a flag de proposito.
    Herdar diretamente tambem quebrava na pratica: o unittest roda os
    metodos de teste em ordem alfabetica, e
    test_shutdown_closes_databases_and_stops_the_server (que derruba o
    servidor de proposito) ordena antes de
    test_shutdown_route_is_absent_without_the_flag, entao o teste herdado
    encontrava um processo ja morto e falhava com connection refused em vez
    de exercitar a asserção que pretendia checar.
    """

    process: subprocess.Popen[bytes] | None = None
    extra_args: list[str] = []

    @classmethod
    def setUpClass(cls) -> None:
        noxy_exe = os.environ.get("NOXY_EXE")
        if not noxy_exe:
            raise RuntimeError("NOXY_EXE must point to the Noxy executable")

        cls.noxy_exe = noxy_exe
        cls.project_root = Path(__file__).resolve().parents[2]
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp_dir.cleanup)
        cls.data_dir = Path(cls.temp_dir.name)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            cls.port = reservation.getsockname()[1]

        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.client = NoxyDBClient(cls.base_url)
        cls.addClassCleanup(cls._stop_server)
        cls._start_server()
        cls._wait_until_healthy()

    @classmethod
    def _start_server(cls) -> None:
        cls.process = subprocess.Popen(
            [
                cls.noxy_exe,
                "server/noxydb_server.nx",
                "--data-dir",
                str(cls.data_dir),
                "--port",
                str(cls.port),
                *cls.extra_args,
            ],
            cwd=cls.project_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @classmethod
    def _stop_server(cls) -> None:
        process = cls.process
        cls.process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    @classmethod
    def _wait_until_healthy(cls) -> None:
        deadline = time.monotonic() + 5
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if cls.process is not None and cls.process.poll() is not None:
                raise RuntimeError(
                    f"NoxyDB server exited with code {cls.process.returncode}"
                )
            try:
                if cls.client.health():
                    return
            except Exception as error:
                last_error = error
            time.sleep(0.05)
        raise RuntimeError("NoxyDB server did not become healthy") from last_error

    @classmethod
    def _restart_server(cls) -> None:
        cls._stop_server()
        cls._start_server()
        cls._wait_until_healthy()

    @classmethod
    def _raw_http(cls, parts: list[bytes]) -> bytes:
        with socket.create_connection(("127.0.0.1", cls.port), timeout=5) as connection:
            for part in parts:
                connection.sendall(part)
            connection.shutdown(socket.SHUT_WR)
            chunks: list[bytes] = []
            while True:
                try:
                    chunk = connection.recv(65536)
                except ConnectionResetError:
                    return b"".join(chunks)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)


class IntegrationTests(_ServerHarness):
    def test_crud_unicode_and_complete_replacement(self) -> None:
        db = self.client.open_database("usuarios")
        self.assertEqual(
            db.put(
                "usuário:1",
                {
                    "name": "Estevão",
                    "profile": {"city": "Cuiabá"},
                    "languages": ["Python", "Noxy"],
                    "active": True,
                },
            ),
            PutResult(True, ""),
        )
        self.assertEqual(db.get("usuário:1").value["profile"]["city"], "Cuiabá")
        db.put("usuário:1", {"name": "Estevão Fonseca"})
        self.assertNotIn("profile", db.get("usuário:1").value)
        db.remove("usuário:1")
        self.assertFalse(db.exists("usuário:1"))

    def test_multiple_databases_are_created_and_isolated(self) -> None:
        users = self.client.open_database("usuarios")
        orders = self.client.open_database("pedidos")
        users.put("same-key", {"kind": "user"})
        orders.put("same-key", {"kind": "order"})
        self.assertEqual(users.get("same-key").value["kind"], "user")
        self.assertEqual(orders.get("same-key").value["kind"], "order")
        self.assertTrue((self.data_dir / "usuarios.db").exists())
        self.assertTrue((self.data_dir / "pedidos.db").exists())

    def test_concurrent_clients_are_serialized_without_lost_documents(self) -> None:
        self.client.open_database("parallel")

        def write(index: int) -> None:
            db = NoxyDBClient(self.base_url).open_database("parallel")
            db.put(f"key:{index}", {"index": index})

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(40)))

        check = self.client.open_database("parallel")
        self.assertTrue(
            all(
                check.get(f"key:{index}").value["index"] == index
                for index in range(40)
            )
        )

    def test_incomplete_client_does_not_block_other_clients(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port), timeout=15) as stalled:
            # header_timeout_ms da stdlib e 5000ms; a espera precisa passar disso.
            stalled.settimeout(15)
            stalled.sendall(b"GET /v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\n")
            responsive_client = NoxyDBClient(self.base_url, timeout=0.5)
            self.assertTrue(responsive_client.health())
            chunks: list[bytes] = []
            while True:
                chunk = stalled.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)

        response = b"".join(chunks)
        self.assertTrue(response.startswith(b"HTTP/1.1 408 "))
        self.assertIn(b"text/plain", response.lower())

    def test_persistence_after_daemon_restart(self) -> None:
        db = self.client.open_database("persistent")
        db.put("key", {"value": "survives"})
        self._restart_server()
        reopened = self.client.open_database("persistent")
        self.assertEqual(reopened.get("key").value, {"value": "survives"})

    def test_fragmented_http_request_is_assembled(self) -> None:
        response = self._raw_http(
            [
                b"POST /v1/open HTTP/1.1\r\nHost: 127.0.0.1\r\n",
                b"Content-Length: 25\r\n\r\n{\"database\":",
                b"\"fragmented\"}",
            ]
        )
        self.assertTrue(response.startswith(b"HTTP/1.1 200 OK\r\n"))
        self.assertIn(b'{"success":true', response)

    def test_bytes_after_the_declared_body_are_discarded(self) -> None:
        """A stdlib descarta bytes alem do Content-Length declarado.

        O transporte artesanal anterior respondia 400 e nao roteava. Trocamos
        esse rigor de pipelining por 328 linhas de framing proprio; este teste
        fixa o comportamento novo em vez de deixa-lo implicito.
        """
        response = self._raw_http(
            [
                b"POST /v1/open HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 22\r\n\r\n",
                b'{"database":"surplus"}',
                b"GET /v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
            ]
        )
        self.assertTrue(response.startswith(b"HTTP/1.1 200 OK\r\n"))
        self.assertIn(b'{"success":true', response)
        self.assertTrue((self.data_dir / "surplus.db").exists())

    def test_declared_request_over_one_mib_is_rejected(self) -> None:
        response = self._raw_http(
            [
                b"POST /v1/put HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Length: 1048577\r\n"
                b"\r\n"
            ]
        )
        self.assertTrue(response.startswith(b"HTTP/1.1 413 "))
        self.assertIn(b"text/plain", response.lower())

    def test_server_rejects_invalid_database_name(self) -> None:
        with self.assertRaises(NoxyDBServerError) as raised:
            self.client._request("/v1/open", {"database": "../outside"})
        self.assertEqual(raised.exception.status, 400)

    def test_invalid_log_error_does_not_expose_data_path(self) -> None:
        invalid_path = self.data_dir / "broken.db"
        invalid_path.write_bytes(b"P\t00")
        with self.assertRaises(NoxyDBServerError) as raised:
            self.client.open_database("broken")
        self.assertEqual(raised.exception.status, 500)
        self.assertNotIn(str(self.data_dir), str(raised.exception))

    def test_server_prints_safe_activity_lines(self) -> None:
        with tempfile.TemporaryDirectory() as activity_data_dir:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
                reservation.bind(("127.0.0.1", 0))
                activity_port = reservation.getsockname()[1]

            process = subprocess.Popen(
                [
                    self.noxy_exe,
                    "server/noxydb_server.nx",
                    "--data-dir",
                    activity_data_dir,
                    "--port",
                    str(activity_port),
                ],
                cwd=self.project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self.assertIsNotNone(process.stdout)
            captured_lines: list[str] = []

            def capture_output() -> None:
                assert process.stdout is not None
                for raw_line in process.stdout:
                    captured_lines.append(raw_line.decode("utf-8", errors="replace").rstrip())

            output_thread = threading.Thread(target=capture_output, daemon=True)
            output_thread.start()

            def wait_for_activity(expected: str) -> None:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if any(expected in line for line in captured_lines):
                        return
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                self.fail("\n".join(captured_lines))

            activity_client = NoxyDBClient(f"http://127.0.0.1:{activity_port}")
            try:
                deadline = time.monotonic() + 5
                while True:
                    try:
                        if activity_client.health():
                            break
                    except Exception:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise
                        time.sleep(0.05)

                wait_for_activity("GET /v1/health status=200")
                database = activity_client.open_database("activity")
                wait_for_activity("POST /v1/open database=activity status=200")
                database.put("line\nbreak", {"password": "must-not-be-logged"})
                wait_for_activity(
                    'POST /v1/put database=activity key="line\\nbreak" status=200'
                )
                database.get("line\nbreak")
                wait_for_activity(
                    'POST /v1/get database=activity key="line\\nbreak" status=200'
                )
                database.exists("line\nbreak")
                wait_for_activity(
                    'POST /v1/exists database=activity key="line\\nbreak" status=200'
                )
                database.remove("line\nbreak")
                wait_for_activity(
                    'POST /v1/remove database=activity key="line\\nbreak" status=200'
                )
                database.close()
                wait_for_activity("POST /v1/close database=activity status=200")

                # These two requests are malformed at the HTTP framing level
                # (duplicate Content-Length; body shorter than declared).
                # The stdlib http_server rejects them before handler() ever
                # runs, so unlike the old hand-rolled transport, no
                # "POST ... status=400" activity line is produced for them --
                # only the raw 400 response below. Asserting that stays
                # consistent with test_declared_request_over_one_mib_is_rejected
                # and test_incomplete_client_does_not_block_other_clients.
                with socket.create_connection(
                    ("127.0.0.1", activity_port), timeout=5
                ) as connection:
                    connection.sendall(
                        b"POST /v1/open HTTP/1.1\r\n"
                        b"Content-Length: 0\r\nContent-Length: 0\r\n\r\n"
                    )
                    connection.shutdown(socket.SHUT_WR)
                    malformed_open_chunks: list[bytes] = []
                    while True:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        malformed_open_chunks.append(chunk)
                self.assertTrue(
                    b"".join(malformed_open_chunks).startswith(b"HTTP/1.1 400 ")
                )

                with socket.create_connection(
                    ("127.0.0.1", activity_port), timeout=5
                ) as connection:
                    connection.sendall(
                        b"POST /v1/put HTTP/1.1\r\nContent-Length: 100\r\n\r\n{}"
                    )
                    connection.shutdown(socket.SHUT_WR)
                    malformed_put_chunks: list[bytes] = []
                    while True:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        malformed_put_chunks.append(chunk)
                self.assertTrue(
                    b"".join(malformed_put_chunks).startswith(b"HTTP/1.1 400 ")
                )
            finally:
                process.terminate()
                process.wait(timeout=5)
                output_thread.join(timeout=2)
                if process.stdout is not None:
                    process.stdout.close()

        output = "\n".join(captured_lines)
        activity_lines = [
            line for line in output.splitlines() if "duration_ms=" in line
        ]
        timestamped_line = re.compile(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} .+ "
            r"status=\d{3} duration_ms=\d+"
        )
        self.assertTrue(all(timestamped_line.fullmatch(line) for line in activity_lines))

        expected_activities = (
            "GET /v1/health status=200",
            "POST /v1/open database=activity status=200",
            'POST /v1/put database=activity key="line\\nbreak" status=200',
            'POST /v1/get database=activity key="line\\nbreak" status=200',
            'POST /v1/exists database=activity key="line\\nbreak" status=200',
            'POST /v1/remove database=activity key="line\\nbreak" status=200',
            "POST /v1/close database=activity status=200",
        )
        for expected in expected_activities:
            self.assertEqual(
                sum(expected in line for line in activity_lines), 1, output
            )

        self.assertNotIn("must-not-be-logged", output)

    def test_shutdown_route_is_absent_without_the_flag(self) -> None:
        with self.assertRaises(NoxyDBServerError) as captured:
            self.client.shutdown()
        self.assertEqual(captured.exception.status, 404)


def _logged_keys(path: Path) -> set[str]:
    """Chaves dos registros P do log, sem aplicar as remocoes por D."""
    keys: set[str] = set()
    for line in path.read_bytes().split(b"\n"):
        if not line:
            continue
        fields = line.split(b"\t")
        if fields[0] == b"P":
            keys.add(bytes.fromhex(fields[1].decode("ascii")).decode("utf-8"))
    return keys


class ShutdownRouteTests(_ServerHarness):
    extra_args = ["--enable-shutdown"]

    def test_shutdown_closes_databases_and_stops_the_server(self) -> None:
        db = self.client.open_database("shutdown_db")
        db.put("user:1", {"name": "Estevao"})
        self.client.shutdown()
        self.assertIsNotNone(self.process)
        self.process.wait(timeout=15)
        self.assertEqual(self.process.returncode, 0)
        log_path = self.data_dir / "shutdown_db.db"
        self.assertTrue(log_path.exists())
        self.assertIn("user:1", _logged_keys(log_path))


if __name__ == "__main__":
    unittest.main()
