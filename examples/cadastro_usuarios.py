from __future__ import annotations

import os
from typing import Any

from noxydb import Database, NoxyDBClient, NoxyDBError, PutResult


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
