# Python User Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a faithful Python port of the interactive Noxy user registry that stores its documents through the local NoxyDB server.

**Architecture:** Keep the example as one import-safe script whose small functions mirror the Noxy source. Use the existing `NoxyDBClient`/`Database` API directly, preserve the `usuarios:meta` index and `usuario:<id>` documents, and translate client exceptions into the original Portuguese CLI error flow.

**Tech Stack:** Python 3.11, Python standard library, the repository's dependency-free `noxydb` Python package, and `unittest`.

## Global Constraints

- Preserve the menu, prompts, messages, data layout, and operation order from `examples/cadastro_usuarios.nx`.
- Connect to `http://127.0.0.1:8765` through the client's default URL and open logical database `usuarios`.
- Keep `examples/cadastro_usuarios.nx` unchanged.
- Do not add input validation, command-line options, environment configuration, transactions, domain classes, or another package.
- Keep user and metadata writes separate and keep IDs monotonic.
- Use `if __name__ == "__main__"` so importing the module has no interactive side effects.
- Preserve all unrelated untracked files and worktree changes.

---

## File Structure

- Create `examples/cadastro_usuarios.py`: server-backed interactive registry and all behavior equivalent to the Noxy example.
- Create `python/tests/test_cadastro_usuarios.py`: isolated behavior tests for registry helpers, CRUD flows, failures, and the executable boundary.
- Modify `README.md`: document how to run the Python port while retaining the embedded Noxy example.

### Task 1: Registry data model and CRUD operations

**Files:**
- Create: `examples/cadastro_usuarios.py`
- Create: `python/tests/test_cadastro_usuarios.py`

**Interfaces:**
- Consumes: `noxydb.Database`, `noxydb.LookupResult`, `noxydb.NoxyDBError`, and `noxydb.PutResult`.
- Produces: `user_key(id: int) -> str`, `new_metadata() -> dict[str, Any]`, `load_metadata(db: Database) -> dict[str, Any]`, `save_metadata(db: Database, next_id: int, ids: list[Any]) -> None`, `add_user(db: Database) -> None`, `list_users(db: Database) -> None`, `remove_user(db: Database) -> None`, and `update_user(db: Database) -> None`.

- [ ] **Step 1: Create the in-memory database test double and failing data-helper tests**

Create `python/tests/test_cadastro_usuarios.py` with imports, a storage-isolating test double, and these first tests:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the helper tests and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m unittest python.tests.test_cadastro_usuarios -v
```

Expected: FAIL with `ImportError: cannot import name 'cadastro_usuarios' from 'examples'` because the Python port does not exist.

- [ ] **Step 3: Implement only the error and metadata helpers**

Create `examples/cadastro_usuarios.py` with:

```python
from __future__ import annotations

from typing import Any

from noxydb import Database, NoxyDBError, PutResult


def fail(db: Database, message: str) -> None:
    print("Erro: " + message)
    try:
        db.close()
    except NoxyDBError:
        pass
    raise SystemExit(1)


def require_put(db: Database, result: PutResult, operation: str) -> None:
    if not result.success:
        fail(db, operation + ": " + result.error)


def user_key(id: int) -> str:
    return "usuario:" + str(id)


def new_metadata() -> dict[str, Any]:
    return {"next_id": 1, "ids": []}


def load_metadata(db: Database) -> dict[str, Any]:
    try:
        result = db.get("usuarios:meta")
    except NoxyDBError as error:
        fail(db, "falha ao ler o índice: " + str(error))
    if result.found:
        return result.value
    metadata = new_metadata()
    operation = "falha ao criar o índice"
    try:
        result = db.put("usuarios:meta", metadata)
    except NoxyDBError as error:
        fail(db, operation + ": " + str(error))
    require_put(db, result, operation)
    return metadata


def save_metadata(db: Database, next_id: int, ids: list[Any]) -> None:
    metadata = {"next_id": next_id, "ids": ids}
    operation = "falha ao atualizar o índice"
    try:
        result = db.put("usuarios:meta", metadata)
    except NoxyDBError as error:
        fail(db, operation + ": " + str(error))
    require_put(db, result, operation)
```

- [ ] **Step 4: Run the helper tests and verify GREEN**

Run the command from Step 2.

Expected: 2 tests PASS.

- [ ] **Step 5: Add failing tests for add and list behavior**

Add these methods to `RegistryTests` before the module's `unittest.main()` block:

```python
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
```

- [ ] **Step 6: Run the add/list tests and verify RED**

Run the command from Step 2.

Expected: the original 2 tests PASS and 3 tests ERROR with missing `add_user` and `list_users` attributes.

- [ ] **Step 7: Implement add and list faithfully**

Append to `examples/cadastro_usuarios.py`:

```python
def add_user(db: Database) -> None:
    print("Adicionar Usuário")
    nome = input("Digite o nome: ")
    email = input("Digite o email: ")
    cargo = input("Digite o cargo: ")
    metadata = load_metadata(db)
    next_id = metadata["next_id"]
    ids = metadata["ids"]
    user = {"id": next_id, "nome": nome, "email": email, "cargo": cargo}
    operation = "falha ao gravar usuário"
    try:
        result = db.put(user_key(next_id), user)
    except NoxyDBError as error:
        fail(db, operation + ": " + str(error))
    require_put(db, result, operation)
    ids.append(next_id)
    # O usuário e o índice são gravações separadas porque NoxyDB não possui transações.
    save_metadata(db, next_id + 1, ids)
    print("Usuário adicionado com ID " + str(next_id))


def list_users(db: Database) -> None:
    print("\nListar Usuários")
    metadata = load_metadata(db)
    ids = metadata["ids"]
    if len(ids) == 0:
        print("Nenhum usuário cadastrado.")
    for id in ids:
        try:
            result = db.get(user_key(id))
        except NoxyDBError as error:
            fail(db, "falha ao listar usuários: " + str(error))
        if result.found:
            user = result.value
            print(
                "ID: "
                + str(user["id"])
                + ", Nome: "
                + str(user["nome"])
                + ", Email: "
                + str(user["email"])
                + ", Cargo: "
                + str(user["cargo"])
            )
    input("\nPressione Enter para continuar...")
```

- [ ] **Step 8: Run the add/list tests and verify GREEN**

Run the command from Step 2.

Expected: 5 tests PASS.

- [ ] **Step 9: Add failing remove, update, and failed-write tests**

Add to `RegistryTests`:

```python
    def test_remove_user_deletes_document_but_preserves_next_id(self) -> None:
        db = MemoryDatabase()
        db.documents = {
            "usuarios:meta": {"next_id": 3, "ids": [1, 2]},
            "usuario:1": {"id": 1},
            "usuario:2": {"id": 2},
        }

        with patch("builtins.input", return_value="1"):
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
```

- [ ] **Step 10: Run remove/update/failure tests and verify RED**

Run the command from Step 2.

Expected: 6 earlier tests PASS (including the failure helper) and four tests ERROR because `remove_user` and `update_user` are missing.

- [ ] **Step 11: Implement remove and update faithfully**

Append to `examples/cadastro_usuarios.py`:

```python
def remove_user(db: Database) -> None:
    print("Remover Usuário")
    id = int(input("Digite o id: "))
    key = user_key(id)
    try:
        present = db.exists(key)
    except NoxyDBError as error:
        fail(db, "falha ao remover usuário: " + str(error))
    if not present:
        print("Usuário não encontrado.")
        return
    try:
        db.remove(key)
    except NoxyDBError as error:
        fail(db, "falha ao remover usuário: " + str(error))
    metadata = load_metadata(db)
    ids = metadata["ids"]
    filtered = [stored_id for stored_id in ids if stored_id != id]
    save_metadata(db, metadata["next_id"], filtered)
    print("Usuário removido.")


def update_user(db: Database) -> None:
    print("Atualizar Usuário")
    id = int(input("Digite o id: "))
    key = user_key(id)
    try:
        present = db.exists(key)
    except NoxyDBError as error:
        fail(db, "falha ao atualizar usuário: " + str(error))
    if not present:
        print("Usuário não encontrado.")
        return
    nome = input("Digite o nome: ")
    email = input("Digite o email: ")
    cargo = input("Digite o cargo: ")
    user = {"id": id, "nome": nome, "email": email, "cargo": cargo}
    operation = "falha ao atualizar usuário"
    try:
        result = db.put(key, user)
    except NoxyDBError as error:
        fail(db, operation + ": " + str(error))
    require_put(db, result, operation)
    print("Usuário atualizado.")
```

- [ ] **Step 12: Run all registry operation tests and verify GREEN**

Run the command from Step 2.

Expected: 10 tests PASS.

- [ ] **Step 13: Commit the independently tested registry operations**

```powershell
git add -- examples/cadastro_usuarios.py python/tests/test_cadastro_usuarios.py
git commit -m "feat: port user registry operations to Python"
```

### Task 2: Executable menu and documentation

**Files:**
- Modify: `examples/cadastro_usuarios.py`
- Modify: `python/tests/test_cadastro_usuarios.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1's CRUD functions and `NoxyDBClient(base_url="http://127.0.0.1:8765")` defaults.
- Produces: `main() -> int` and an executable `if __name__ == "__main__"` boundary.

- [ ] **Step 1: Add failing tests for import safety and the menu lifecycle**

Add `runpy` and `Path` imports, then add these methods to `RegistryTests`:

```python
    def test_loading_as_non_main_does_not_connect_or_run_menu(self) -> None:
        import runpy
        from pathlib import Path

        script = Path(cadastro_usuarios.__file__)
        with patch("noxydb.NoxyDBClient") as client_type:
            runpy.run_path(str(script), run_name="cadastro_usuarios_import_test")
        client_type.assert_not_called()

    def test_main_opens_usuarios_runs_exit_option_and_closes(self) -> None:
        db = MemoryDatabase()
        output = io.StringIO()
        with patch.object(cadastro_usuarios, "NoxyDBClient") as client_type:
            client_type.return_value.open_database.return_value = db
            with patch.object(cadastro_usuarios.os, "system") as clear:
                with patch("builtins.input", side_effect=["x", "5"]):
                    with redirect_stdout(output):
                        status = cadastro_usuarios.main()

        self.assertEqual(status, 0)
        client_type.assert_called_once_with()
        client_type.return_value.open_database.assert_called_once_with("usuarios")
        self.assertEqual(clear.call_args_list, [call("cls")] * 2)
        self.assertTrue(db.closed)
        self.assertIn("Sistema de Gerenciamento de Usuários", output.getvalue())
        self.assertIn("Opção inválida.", output.getvalue())
        self.assertTrue(output.getvalue().rstrip().endswith("Sair"))

    def test_main_reports_open_and_close_failures(self) -> None:
        with self.subTest(operation="open"):
            output = io.StringIO()
            with patch.object(cadastro_usuarios, "NoxyDBClient") as client_type:
                client_type.return_value.open_database.side_effect = NoxyDBConnectionError(
                    "offline"
                )
                with redirect_stdout(output):
                    self.assertEqual(cadastro_usuarios.main(), 1)
            self.assertEqual(output.getvalue().strip(), "Erro ao abrir banco: offline")

        with self.subTest(operation="close"):
            db = MemoryDatabase()
            db.close_error = NoxyDBConnectionError("close failed")
            output = io.StringIO()
            with patch.object(cadastro_usuarios, "NoxyDBClient") as client_type:
                client_type.return_value.open_database.return_value = db
                with patch.object(cadastro_usuarios.os, "system"):
                    with patch("builtins.input", side_effect=["5"]):
                        with redirect_stdout(output):
                            self.assertEqual(cadastro_usuarios.main(), 1)
            self.assertTrue(
                output.getvalue().rstrip().endswith("Erro ao fechar banco: close failed")
            )
```

- [ ] **Step 2: Run the lifecycle tests and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m unittest python.tests.test_cadastro_usuarios -v
```

Expected: Task 1 tests PASS; lifecycle tests ERROR because `NoxyDBClient`, `os`, and `main` are not defined in the example.

- [ ] **Step 3: Implement the executable menu**

Add `import os` and `NoxyDBClient` to the script imports, then append:

```python
def main() -> int:
    client = NoxyDBClient()
    try:
        db = client.open_database("usuarios")
    except NoxyDBError as error:
        print("Erro ao abrir banco: " + str(error))
        return 1

    load_metadata(db)

    running = True
    while running:
        os.system("cls")
        print("")
        print("Sistema de Gerenciamento de Usuários")
        print("====================================")
        print("1. Adicionar Usuário")
        print("2. Listar Usuários")
        print("3. Remover Usuário")
        print("4. Atualizar Usuário")
        print("5. Sair")
        option = input("Digite a opção desejada: ")

        if option == "1":
            add_user(db)
        if option == "2":
            list_users(db)
        if option == "3":
            remove_user(db)
        if option == "4":
            update_user(db)
        if option == "5":
            running = False
        if option not in ("1", "2", "3", "4", "5"):
            print("Opção inválida.")

    try:
        db.close()
    except NoxyDBError as error:
        print("Erro ao fechar banco: " + str(error))
        return 1
    print("Sair")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the complete example test file and verify GREEN**

Run the command from Step 2.

Expected: 13 tests PASS.

- [ ] **Step 5: Update the README run instructions**

Replace the current interactive registry invocation block and following
storage sentence with:

````markdown
Start the local server in another terminal, then run the Python port:

```powershell
& $env:NOXY_EXE server/noxydb_server.nx --data-dir .\data --port 8765
python examples/cadastro_usuarios.py
```

The Python example opens the logical database `usuarios`, stored by the server
as `data/usuarios.db`. The embedded Noxy version remains available for
comparison and stores its database at `examples/usuarios.db`:

```powershell
& $env:NOXY_EXE examples/cadastro_usuarios.nx
```

Both generated database files are ignored by Git.
````

- [ ] **Step 6: Run focused and full Python unit verification**

Run:

```powershell
& .\.venv\Scripts\python.exe -m unittest python.tests.test_cadastro_usuarios -v
& .\.venv\Scripts\python.exe -m unittest discover -s python/tests -p "test_*.py"
git diff --check
```

Expected: 13 registry tests PASS; 44 total Python unit tests PASS with no
failures; `git diff --check` produces no output.

- [ ] **Step 7: Smoke-test the real server-backed exit path**

In one terminal, start the local server:

```powershell
& $env:NOXY_EXE server/noxydb_server.nx --data-dir .\data --port 8765
```

In a second terminal, run:

```powershell
"5" | & .\.venv\Scripts\python.exe examples/cadastro_usuarios.py
```

Expected: the menu appears, the process exits successfully, and the last line
is `Sair`. If `NOXY_EXE` is unavailable, record the smoke test as blocked and
rely on the already existing end-to-end client suite rather than claiming the
manual smoke test passed.

- [ ] **Step 8: Review the final diff against the approved design**

Run:

```powershell
git diff -- examples/cadastro_usuarios.py python/tests/test_cadastro_usuarios.py README.md
git status --short
```

Confirm the `.nx` example is untouched, all Portuguese copy matches it, only
the approved files changed, and unrelated untracked files remain unmodified.

- [ ] **Step 9: Commit the menu and documentation**

```powershell
git add -- examples/cadastro_usuarios.py python/tests/test_cadastro_usuarios.py README.md
git commit -m "docs: add server-backed user registry example"
```
