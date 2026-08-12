# NoxyDB User Registry Example Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the interactive Noxy SQLite user registry to NoxyDB using one document per user and an explicit metadata index.

**Architecture:** `examples/cadastro_usuarios.nx` stores users under `usuario:<id>` and stores the monotonic ID counter plus enumerable IDs under `usuarios:meta`. Every menu operation uses only the public NoxyDB API, checks observable results, and persists through the existing append-only log.

**Tech Stack:** Noxy, NoxyDB v0.2, PowerShell process-driven integration verification.

## Global Constraints

- Preserve the Portuguese add, list, remove, update, and exit workflow.
- Store each user as a JSON document under `usuario:<id>`.
- Store `next_id` and `ids` in the `usuarios:meta` document.
- Keep IDs monotonically increasing and do not reuse removed IDs.
- Use only the public NoxyDB API; do not access `DatabaseState` or raw payloads.
- Keep `examples/documents.nx` unchanged.
- Persist to `examples/usuarios.db`, which must remain ignored by Git.
- Report a metadata write failure and exit because NoxyDB has no transactions.

---

### Task 1: Interactive user registry

**Files:**
- Create: `examples/cadastro_usuarios.nx`
- Modify: `README.md`

**Interfaces:**
- Consumes: `noxydb.open_database`, `put`, `get`, `exists`, `remove`, `close_database`, `is_open`, and `database_error`.
- Produces: keys `usuarios:meta` and `usuario:<id>` inside `examples/usuarios.db`.

- [ ] **Step 1: Verify RED because the port is absent**

```powershell
Test-Path examples/cadastro_usuarios.nx
```

Expected: `False`.

- [ ] **Step 2: Create the complete NoxyDB port**

Create `examples/cadastro_usuarios.nx`:

```noxy
use noxydb
use sys

func fail(db: noxydb.Database, message: string) -> void
    print("Erro: " + message)
    noxydb.close_database(db)
    sys.exit(1)
end

func require_put(db: noxydb.Database, result: noxydb.PutResult, operation: string) -> void
    if !result.success then
        fail(db, operation + ": " + result.error)
    end
end

func user_key(id: int) -> string
    return "usuario:" + to_str(id)
end

func new_metadata() -> map[string, any]
    let ids: any[]
    return {"next_id": 1, "ids": ids}
end

func load_metadata(db: noxydb.Database) -> map[string, any]
    let result: noxydb.LookupResult = noxydb.get(db, "usuarios:meta")
    if result.found then
        return result.value
    end
    if !noxydb.is_open(db) then
        fail(db, "falha ao ler o índice: " + noxydb.database_error(db))
    end
    let metadata: map[string, any] = new_metadata()
    require_put(db, noxydb.put(db, "usuarios:meta", metadata), "falha ao criar o índice")
    return metadata
end

func save_metadata(db: noxydb.Database, next_id: int, ids: any[]) -> void
    let metadata: map[string, any] = {"next_id": next_id, "ids": ids}
    require_put(db, noxydb.put(db, "usuarios:meta", metadata), "falha ao atualizar o índice")
end

func add_user(db: noxydb.Database) -> void
    print("Adicionar Usuário")
    let nome: string = input("Digite o nome: ")
    let email: string = input("Digite o email: ")
    let cargo: string = input("Digite o cargo: ")
    let metadata: map[string, any] = load_metadata(db)
    let next_id: int = metadata["next_id"]
    let ids: any[] = metadata["ids"]
    let user: map[string, any] = {
        "id": next_id,
        "nome": nome,
        "email": email,
        "cargo": cargo
    }
    require_put(db, noxydb.put(db, user_key(next_id), user), "falha ao gravar usuário")
    append(ids, next_id)
    // O usuário e o índice são gravações separadas porque NoxyDB não possui transações.
    save_metadata(db, next_id + 1, ids)
    print("Usuário adicionado com ID " + to_str(next_id))
end

func list_users(db: noxydb.Database) -> void
    print("\nListar Usuários")
    let metadata: map[string, any] = load_metadata(db)
    let ids: any[] = metadata["ids"]
    if length(ids) == 0 then
        print("Nenhum usuário cadastrado.")
    end
    let index: int = 0
    while index < length(ids) do
        let id: int = ids[index]
        let result: noxydb.LookupResult = noxydb.get(db, user_key(id))
        if result.found then
            print("ID: " + to_str(result.value["id"]) +
                ", Nome: " + to_str(result.value["nome"]) +
                ", Email: " + to_str(result.value["email"]) +
                ", Cargo: " + to_str(result.value["cargo"]))
        elif !noxydb.is_open(db) then
            fail(db, "falha ao listar usuários: " + noxydb.database_error(db))
        end
        index = index + 1
    end
    input("\nPressione Enter para continuar...")
end

func remove_user(db: noxydb.Database) -> void
    print("Remover Usuário")
    let id: int = to_int(input("Digite o id: "))
    let key: string = user_key(id)
    if !noxydb.exists(db, key) then
        print("Usuário não encontrado.")
        return
    end
    noxydb.remove(db, key)
    if !noxydb.is_open(db) then
        fail(db, "falha ao remover usuário: " + noxydb.database_error(db))
    end
    let metadata: map[string, any] = load_metadata(db)
    let ids: any[] = metadata["ids"]
    let filtered: any[]
    let index: int = 0
    while index < length(ids) do
        if ids[index] != id then append(filtered, ids[index]) end
        index = index + 1
    end
    save_metadata(db, metadata["next_id"], filtered)
    print("Usuário removido.")
end

func update_user(db: noxydb.Database) -> void
    print("Atualizar Usuário")
    let id: int = to_int(input("Digite o id: "))
    let key: string = user_key(id)
    if !noxydb.exists(db, key) then
        print("Usuário não encontrado.")
        return
    end
    let nome: string = input("Digite o nome: ")
    let email: string = input("Digite o email: ")
    let cargo: string = input("Digite o cargo: ")
    let user: map[string, any] = {
        "id": id,
        "nome": nome,
        "email": email,
        "cargo": cargo
    }
    require_put(db, noxydb.put(db, key, user), "falha ao atualizar usuário")
    print("Usuário atualizado.")
end

let db: noxydb.Database = noxydb.open_database("examples/usuarios.db")
if !noxydb.is_open(db) then
    print("Erro ao abrir banco: " + noxydb.database_error(db))
    sys.exit(1)
end
load_metadata(db)

let running: bool = true
while running do
    sys.exec("cls")
    print("")
    print("Sistema de Gerenciamento de Usuários")
    print("====================================")
    print("1. Adicionar Usuário")
    print("2. Listar Usuários")
    print("3. Remover Usuário")
    print("4. Atualizar Usuário")
    print("5. Sair")
    let option: string = input("Digite a opção desejada: ")

    if option == "1" then add_user(db) end
    if option == "2" then list_users(db) end
    if option == "3" then remove_user(db) end
    if option == "4" then update_user(db) end
    if option == "5" then running = false end
    if option != "1" && option != "2" && option != "3" && option != "4" && option != "5" then
        print("Opção inválida.")
    end
end

noxydb.close_database(db)
if noxydb.database_error(db) != "" then
    print("Erro ao fechar banco: " + noxydb.database_error(db))
    sys.exit(1)
end
print("Sair")
```

- [ ] **Step 3: Add the README entry**

Add after the existing executable-example text:

~~~~markdown
### Cadastro interativo de usuários

`examples/cadastro_usuarios.nx` porta o cadastro SQLite original para NoxyDB.
Cada usuário ocupa uma chave própria, enquanto `usuarios:meta` mantém o próximo
ID e o índice usado pela listagem:

```powershell
& $env:NOXY_EXE examples/cadastro_usuarios.nx
```

O banco fica em `examples/usuarios.db` e é ignorado pelo Git.
~~~~

- [ ] **Step 4: Verify the interactive flow and replay**

Run this PowerShell driver from the repository root:

```powershell
$noxy = 'D:\OneDrive\Documentos\noxy_projects\noxydb\.worktrees\noxy-json\noxy.exe'
$database = 'examples\usuarios.db'
if (Test-Path -LiteralPath $database) { Remove-Item -LiteralPath $database }

function Invoke-Registry([string[]]$Lines) {
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $noxy
    $info.ArgumentList.Add('examples/cadastro_usuarios.nx')
    $info.WorkingDirectory = (Get-Location).Path
    $info.UseShellExecute = $false
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $info
    if (!$process.Start()) { throw 'Failed to start registry example' }
    foreach ($line in $Lines) {
        # input() creates a reader per prompt; wait until the next prompt is ready.
        Start-Sleep -Milliseconds 1200
        $process.StandardInput.WriteLine($line)
        $process.StandardInput.Flush()
    }
    $process.StandardInput.Close()
    if (!$process.WaitForExit(30000)) {
        $process.Kill()
        throw 'Registry example timed out'
    }
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    if ($process.ExitCode -ne 0) { throw $stdout + $stderr }
    return $stdout + $stderr
}

$first = Invoke-Registry @(
    '1', 'Ana', 'ana@example.com', 'Engenheira',
    '2', '',
    '4', '1', 'Ana Maria', 'ana.maria@example.com', 'Arquiteta',
    '2', '',
    '5'
)
if ($first -notmatch 'Nome: Ana,' -or $first -notmatch 'Nome: Ana Maria,') {
    throw 'Add/list/update flow was not observed'
}

$second = Invoke-Registry @('2', '', '3', '1', '2', '', '5')
if ($second -notmatch 'Nome: Ana Maria,' -or $second -notmatch 'Nenhum usuário cadastrado') {
    throw 'Replay/remove flow was not observed'
}
```

Expected: both processes exit zero; the first observes add and update; the
second observes the updated user from replay, removes it, and lists an empty
registry.

- [ ] **Step 5: Run regression and repository checks**

```powershell
Test-Path examples/usuarios.db
git check-ignore examples/usuarios.db
$env:NOXY_EXE = 'D:\OneDrive\Documentos\noxy_projects\noxydb\.worktrees\noxy-json\noxy.exe'
./tests/run_tests.ps1
git diff --check
```

Expected: the database exists and is ignored, all 16 NoxyDB test files pass,
and the diff check exits zero.

- [ ] **Step 6: Commit**

```powershell
git add README.md examples/cadastro_usuarios.nx
git commit -m "docs: add NoxyDB user registry example"
```
