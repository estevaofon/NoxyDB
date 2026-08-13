from __future__ import annotations

import copy
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import call, patch

from examples import cadastro_usuarios
from noxydb import LookupResult, NoxyDBConnectionError, PutResult


class MemoryDatabase:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, object]] = {}
        self.put_results: list[PutResult] = []
        self.put_error: Exception | None = None
        self.closed = False
        self.close_error: Exception | None = None

    def put(self, key: str, value: dict[str, object]) -> PutResult:
        if self.put_error is not None:
            raise self.put_error
        result = self.put_results.pop(0) if self.put_results else PutResult(True, "")
        if result.success:
            self.documents[key] = copy.deepcopy(value)
        return result

    def get(self, key: str) -> LookupResult:
        if key not in self.documents:
            return LookupResult(False, {})
        return LookupResult(True, copy.deepcopy(self.documents[key]))

    def exists(self, key: str) -> bool:
        return key in self.documents

    def remove(self, key: str) -> None:
        self.documents.pop(key, None)

    def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


class RegistryTests(unittest.TestCase):
    def test_metadata_shape_and_user_key_match_noxy_example(self) -> None:
        self.assertEqual(cadastro_usuarios.user_key(12), "usuario:12")
        self.assertEqual(
            cadastro_usuarios.new_metadata(),
            {"next_id": 1, "ids": []},
        )

    def test_load_metadata_creates_it_once_and_returns_stored_value(self) -> None:
        db = MemoryDatabase()

        created = cadastro_usuarios.load_metadata(db)
        created["next_id"] = 99
        loaded = cadastro_usuarios.load_metadata(db)

        self.assertEqual(created, {"next_id": 99, "ids": []})
        self.assertEqual(loaded, {"next_id": 1, "ids": []})
        self.assertEqual(db.documents["usuarios:meta"], {"next_id": 1, "ids": []})

    def test_add_user_writes_user_then_advances_metadata(self) -> None:
        db = MemoryDatabase()
        cadastro_usuarios.load_metadata(db)

        output = io.StringIO()
        with patch("builtins.input", side_effect=["Ana", "ana@example.com", "Dev"]):
            with redirect_stdout(output):
                cadastro_usuarios.add_user(db)

        self.assertEqual(
            db.documents["usuario:1"],
            {"id": 1, "nome": "Ana", "email": "ana@example.com", "cargo": "Dev"},
        )
        self.assertEqual(db.documents["usuarios:meta"], {"next_id": 2, "ids": [1]})
        self.assertIn("Usuário adicionado com ID 1", output.getvalue())

    def test_list_users_follows_index_order_and_skips_stale_ids(self) -> None:
        db = MemoryDatabase()
        db.documents = {
            "usuarios:meta": {"next_id": 4, "ids": [2, 3, 1]},
            "usuario:1": {"id": 1, "nome": "Ana", "email": "a@x", "cargo": "Dev"},
            "usuario:2": {"id": 2, "nome": "Bia", "email": "b@x", "cargo": "QA"},
        }

        output = io.StringIO()
        with patch("builtins.input", return_value="") as pause:
            with redirect_stdout(output):
                cadastro_usuarios.list_users(db)

        text = output.getvalue()
        self.assertLess(text.index("ID: 2"), text.index("ID: 1"))
        self.assertNotIn("ID: 3", text)
        pause.assert_called_once_with("\nPressione Enter para continuar...")

    def test_list_users_reports_empty_registry_and_pauses(self) -> None:
        db = MemoryDatabase()

        output = io.StringIO()
        with patch("builtins.input", return_value=""):
            with redirect_stdout(output):
                cadastro_usuarios.list_users(db)

        self.assertIn("Nenhum usuário cadastrado.", output.getvalue())

    def test_remove_user_deletes_document_but_preserves_next_id(self) -> None:
        db = MemoryDatabase()
        db.documents = {
            "usuarios:meta": {"next_id": 3, "ids": [1, 2]},
            "usuario:1": {"id": 1},
            "usuario:2": {"id": 2},
        }

        with patch("builtins.input", return_value="1"):
            with redirect_stdout(io.StringIO()):
                cadastro_usuarios.remove_user(db)

        self.assertNotIn("usuario:1", db.documents)
        self.assertEqual(db.documents["usuarios:meta"], {"next_id": 3, "ids": [2]})

    def test_remove_absent_user_returns_without_extra_prompts(self) -> None:
        db = MemoryDatabase()

        output = io.StringIO()
        with patch("builtins.input", side_effect=["7"]) as prompt:
            with redirect_stdout(output):
                cadastro_usuarios.remove_user(db)

        self.assertEqual(prompt.call_count, 1)
        self.assertIn("Usuário não encontrado.", output.getvalue())

    def test_update_user_completely_replaces_document(self) -> None:
        db = MemoryDatabase()
        db.documents["usuario:4"] = {"id": 4, "nome": "old", "extra": True}

        with patch(
            "builtins.input",
            side_effect=["4", "Caio", "caio@example.com", "Arquiteto"],
        ):
            with redirect_stdout(io.StringIO()):
                cadastro_usuarios.update_user(db)

        self.assertEqual(
            db.documents["usuario:4"],
            {
                "id": 4,
                "nome": "Caio",
                "email": "caio@example.com",
                "cargo": "Arquiteto",
            },
        )

    def test_update_absent_user_returns_without_replacement_prompts(self) -> None:
        db = MemoryDatabase()

        with patch("builtins.input", side_effect=["8"]) as prompt:
            with redirect_stdout(io.StringIO()):
                cadastro_usuarios.update_user(db)

        self.assertEqual(prompt.call_count, 1)

    def test_failed_write_prints_error_closes_handle_and_exits(self) -> None:
        for put_result, put_error, message in (
            (PutResult(False, "disk full"), None, "disk full"),
            (None, NoxyDBConnectionError("offline"), "offline"),
        ):
            with self.subTest(message=message):
                db = MemoryDatabase()
                if put_result is not None:
                    db.put_results.append(put_result)
                db.put_error = put_error

                output = io.StringIO()
                with redirect_stdout(output):
                    with self.assertRaisesRegex(SystemExit, "1"):
                        cadastro_usuarios.save_metadata(db, 2, [1])

                self.assertTrue(db.closed)
                self.assertEqual(
                    output.getvalue().strip(),
                    "Erro: falha ao atualizar o índice: " + message,
                )


if __name__ == "__main__":
    unittest.main()
