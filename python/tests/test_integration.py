import os
import socket
import subprocess
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from noxydb import NoxyDBClient, NoxyDBServerError, PutResult


class IntegrationTests(unittest.TestCase):
    process: subprocess.Popen[bytes] | None = None

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
                chunk = connection.recv(65536)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)

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
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as stalled:
            stalled.sendall(b"GET /v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\n")
            responsive_client = NoxyDBClient(self.base_url, timeout=0.5)
            self.assertTrue(responsive_client.health())

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

    def test_declared_request_over_one_mib_is_rejected(self) -> None:
        response = self._raw_http(
            [
                b"POST /v1/put HTTP/1.1\r\nHost: 127.0.0.1\r\n",
                b"Content-Length: 1048577\r\n\r\n",
            ]
        )
        self.assertTrue(response.startswith(b"HTTP/1.1 400 Bad Request\r\n"))

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


if __name__ == "__main__":
    unittest.main()
