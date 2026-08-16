# NoxyDB v0.3.0 sobre Noxy v0.4.0 — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrar o NoxyDB para a semântica de valor com copy-on-write do Noxy v0.4.0, substituir o transporte HTTP artesanal pela stdlib e adicionar encerramento limpo por sinal e por rota.

**Architecture:** Toda função pública do núcleo passa a receber `ref Database`, porque passar o struct por valor faz a escrita seguinte clonar a keyspace inteira. O transporte HTTP artesanal (328 linhas escritas para contornar limitações que a linguagem não tem mais) é apagado em favor de `http_server` da stdlib. O worker de banco continua serializando o acesso por canal, agora supervisionado por `spawn_task`.

**Tech Stack:** Noxy v0.4.0 (`C:\Users\estev\go\bin\noxy.exe`), stdlib `http_server`/`http_parser`/`net`/`sys`, Python 3.11+ (cliente, `unittest`), PowerShell (runner de testes).

**Spec:** `docs/superpowers/specs/2026-08-16-noxydb-v04-refactor-design.md`

## Global Constraints

- **Executável de referência:** `C:\Users\estev\go\bin\noxy.exe`, que reporta `Noxy v0.4.0`. Todo comando de teste exporta `$env:NOXY_EXE` com esse caminho.
- **`ref` é explícito em toda chamada qualificada de módulo.** `noxydb.put(db, ...)` falha com `expected ref Database, got object`. Escreva `noxydb.put(ref db, ...)`. Isso vale **mesmo quando a variável já tem tipo `ref T`** — encaminhar exige `ref handle` de novo.
- **`strings.substring(s, start, end)`** — o terceiro argumento é índice final **exclusivo**, nunca comprimento.
- **`to_str(bytes)` levanta** quando os bytes não são UTF-8 válido. Use `strings.is_valid_utf8(b)` antes de converter qualquer `bytes` de origem externa.
- **Todo `let x: map[...]` / `let x: T[]` declarado sem inicializador** recebe `= {}` (ou `= []`). Sem isso, atravessar fronteira de módulo produz `runtime value metadata conflicts with static context`.
- **Comentários em Noxy usam `//`**, não `--`.
- **Estado em memória permanece `map[string, string]`** com JSON serializado. Não trocar por map parseado.
- **Nunca introduzir parâmetro `Database` por valor.** Medido: 5.198 ms contra 15 ms em N=5000, mesmo quando a função só lê um bool.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade | Ação |
|---|---|---|
| `noxydb/storage.nx` | Log append-only, replay, validação de registro | Modificar |
| `noxydb/document.nx` | Codec JSON do documento | Inalterado |
| `noxydb/noxydb.nx` | API pública do banco | Modificar |
| `server/protocol.nx` | Decode/encode do corpo da API | Modificar |
| `server/database_worker.nx` | Cache de bancos, execução serializada de comandos | Modificar |
| `server/http_transport.nx` | Transporte HTTP artesanal | **Apagar** |
| `server/api.nx` | Handler HTTP, roteamento, log de atividade | **Criar** |
| `server/noxydb_server.nx` | Entrada, opções, bind, sinais, encerramento | Modificar |
| `tests/*.nx` | Testes do núcleo e do servidor | Modificar |
| `tests/api_test.nx` | Testes do handler novo | **Criar** |
| `tests/invalid_hex_utf8_test.nx` | Replay com hex não-UTF-8 | **Criar** |
| `tests/run_tests.ps1` | Runner | Modificar |
| `python/src/noxydb/client.py` | Cliente HTTP | Modificar |
| `python/tests/*.py` | Testes do cliente e integração | Modificar |
| `examples/*.nx` | Exemplos embarcados | Modificar |

---

## Task 1: `storage.nx` — replay seguro a hex não-UTF-8

**Files:**
- Modify: `noxydb/storage.nx:26-28` (`decode_hex`), `:34-86` (`replay`)
- Create: `tests/invalid_hex_utf8_test.nx`
- Modify: `tests/invalid_document_log_test.nx:5-28`
- Modify: `tests/run_tests.ps1:51-56`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces: `storage.DecodeResult` com campos `ok: bool` e `value: string`; `storage.decode_hex(value: string) -> DecodeResult`; `storage.replay(path: string) -> ReplayResult` (assinatura inalterada, novo erro possível `"invalid database log record"` para hex que decodifica em bytes não-UTF-8).

**Contexto:** `decode_hex` faz `to_str(hex_decode(value))`. Desde o v0.3.0 `to_str` levanta sobre bytes não-UTF-8, então um log corrompido derruba o processo em vez de virar erro de replay.

**Mudança de classificação de erro, deliberada:** hoje um payload não-UTF-8 chega até a camada de documento e vira `"invalid document payload"`. Depois desta task ele para na camada de storage e vira `"invalid database log record"`. É a classificação certa: bytes que não são UTF-8 não podem ser uma string Noxy, então o registro é malformado, não o documento.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/invalid_hex_utf8_test.nx`:

```noxy
use io
use noxydb.storage as storage
use tests.assertions as assertions

func write_fixture(path: string, content: string) -> void
    if io.exists(path) then
        io.remove(path)
    end
    let file: io.File = io.open(path, "w")
    io.write(file, content)
    io.close(file)
end

// 0xFF isolado nao e UTF-8 valido; o hex e valido, o conteudo decodificado nao.
let key_path: string = "tests/invalid_hex_key.db"
write_fixture(key_path, "P\tff\t7b7d\n")
defer io.remove(key_path)
let key_result: storage.ReplayResult = storage.replay(key_path)
assertions.assert_false(key_result.success, "chave nao-UTF-8 deve falhar o replay")
assertions.assert_string(key_result.error, "invalid database log record", "chave nao-UTF-8 deve ser registro invalido")

let payload_path: string = "tests/invalid_hex_payload.db"
write_fixture(payload_path, "P\t6b6579\t7b226e616d65223a22ff227d\n")
defer io.remove(payload_path)
let payload_result: storage.ReplayResult = storage.replay(payload_path)
assertions.assert_false(payload_result.success, "payload nao-UTF-8 deve falhar o replay")
assertions.assert_string(payload_result.error, "invalid database log record", "payload nao-UTF-8 deve ser registro invalido")

let delete_path: string = "tests/invalid_hex_delete.db"
write_fixture(delete_path, "P\t6b6579\t7b7d\nD\tff\n")
defer io.remove(delete_path)
let delete_result: storage.ReplayResult = storage.replay(delete_path)
assertions.assert_false(delete_result.success, "chave nao-UTF-8 em D deve falhar o replay")
assertions.assert_string(delete_result.error, "invalid database log record", "chave nao-UTF-8 em D deve ser registro invalido")

print("[PASS] invalid_hex_utf8_test")
```

- [ ] **Step 2: Rodar para confirmar que falha**

```powershell
$env:NOXY_EXE = 'C:\Users\estev\go\bin\noxy.exe'
& $env:NOXY_EXE tests\invalid_hex_utf8_test.nx
```

Esperado: `Runtime error: ... native 'to_str' failed: to_str: bytes are not valid UTF-8 at byte offset 0`

- [ ] **Step 3: Adicionar `DecodeResult` e reescrever `decode_hex`**

Em `noxydb/storage.nx`, adicionar o struct logo após `ReplayResult` e substituir `decode_hex`:

```noxy
struct DecodeResult
    ok: bool
    value: string
end

func decode_hex(value: string) -> DecodeResult
    let raw: bytes = hex_decode(value)
    if !strings.is_valid_utf8(raw) then
        return DecodeResult(false, "")
    end
    return DecodeResult(true, to_str(raw))
end
```

- [ ] **Step 4: Ajustar `replay` aos dois pontos de decodificação**

Substituir o corpo do laço de `replay` (o bloco `while index < lines.count - 1 do`) por:

```noxy
    while index < lines.count - 1 do
        let fields: strings.SplitResult = strings.split(lines.parts[index], "\t")
        if fields.count == 3 && fields.parts[0] == "P" then
            if !is_valid_hex(fields.parts[1]) || !is_valid_hex(fields.parts[2]) then
                return ReplayResult(false, payloads, "invalid database log record")
            end
            let key_decoded: DecodeResult = decode_hex(fields.parts[1])
            let payload_decoded: DecodeResult = decode_hex(fields.parts[2])
            if !key_decoded.ok || !payload_decoded.ok then
                return ReplayResult(false, payloads, "invalid database log record")
            end
            payloads[key_decoded.value] = payload_decoded.value
        elif fields.count == 2 && fields.parts[0] == "D" then
            if !is_valid_hex(fields.parts[1]) then
                return ReplayResult(false, payloads, "invalid database log record")
            end
            let key_decoded: DecodeResult = decode_hex(fields.parts[1])
            if !key_decoded.ok then
                return ReplayResult(false, payloads, "invalid database log record")
            end
            if has_key(payloads, key_decoded.value) then
                delete(payloads, key_decoded.value)
            end
        else
            return ReplayResult(false, payloads, "invalid database log record")
        end
        index = index + 1
    end
```

- [ ] **Step 5: Inicializar o map de `replay`**

Em `noxydb/storage.nx:35`, trocar `let payloads: map[string, string]` por:

```noxy
    let payloads: map[string, string] = {}
```

- [ ] **Step 6: Rodar o teste novo e confirmar que passa**

```powershell
& $env:NOXY_EXE tests\invalid_hex_utf8_test.nx
```

Esperado: `[PASS] invalid_hex_utf8_test`

- [ ] **Step 7: Corrigir o fixture de `invalid_document_log_test.nx`**

O teste quebra no **próprio fixture**: a linha 28 chama `to_str(hex_decode("...ff..."))`, que levanta antes de tocar o código de produção. Substituir o arquivo inteiro por:

```noxy
use io
use noxydb
use tests.assertions as assertions

func write_payload_hex(path: string, payload_hex: string) -> void
    if io.exists(path) then
        io.remove(path)
    end
    let file: io.File = io.open(path, "w")
    io.write(file, "P\t" + hex_encode("key") + "\t" + payload_hex + "\n")
    io.close(file)
end

func assert_invalid_payload(path: string, payload: string) -> void
    write_payload_hex(path, hex_encode(payload))
    let db: noxydb.Database = noxydb.open_database(path)
    assertions.assert_false(noxydb.is_open(db), "invalid payload should fail open")
    assertions.assert_string(noxydb.database_error(db), "invalid document payload", "payload error should be exact")
    assertions.assert_true(length(keys(db.state.payloads)) == 0, "failed open should expose no partial raw state")
    io.remove(path)
end

assert_invalid_payload("tests/malformed_document.db", "{")
assert_invalid_payload("tests/string_root.db", "\"text\"")
assert_invalid_payload("tests/int_root.db", "42")
assert_invalid_payload("tests/array_root.db", "[]")
assert_invalid_payload("tests/null_root.db", "null")
print("[PASS] invalid_document_log_test")
```

O caso `invalid_utf8_document` sai daqui: ele agora é coberto por `invalid_hex_utf8_test.nx` com a classificação nova. As chamadas `noxydb.is_open(db)` e `noxydb.database_error(db)` continuam sem `ref` nesta task e serão migradas na Task 2.

- [ ] **Step 8: Registrar o teste novo no runner**

Em `tests/run_tests.ps1`, no array `$errorTests` (linhas 51-56), adicionar a entrada:

```powershell
$errorTests = @(
    "invalid_log_test.nx",
    "invalid_document_log_test.nx",
    "invalid_hex_utf8_test.nx",
    "open_failure_test.nx",
    "read_size_test.nx"
)
```

- [ ] **Step 9: Rodar o grupo de erros**

```powershell
.\tests\run_tests.ps1 -Group errors
```

Esperado: os 5 arquivos passam. `invalid_document_log_test.nx` ainda pode falhar por causa da API sem `ref` — se falhar, é por `[FAIL]` de asserção e não por `Runtime error`; a Task 2 fecha isso.

- [ ] **Step 10: Commit**

```bash
git add noxydb/storage.nx tests/invalid_hex_utf8_test.nx tests/invalid_document_log_test.nx tests/run_tests.ps1
git commit -m "fix(storage): replay rejeita hex nao-UTF-8 em vez de levantar

to_str levanta sobre bytes nao-UTF-8 desde o v0.3.0, entao um log corrompido
derrubava o processo em vez de virar ReplayResult(false). decode_hex passa a
devolver DecodeResult e o replay classifica como registro invalido.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: `noxydb.nx` — API `ref` e maps inicializados

**Files:**
- Modify: `noxydb/noxydb.nx` (todas as funções mutantes e de leitura)
- Modify: `tests/database_test.nx`, `tests/document_isolation_test.nx`, `tests/write_failure_test.nx`, `tests/close_failure_test.nx`, `tests/deleted_write_test.nx`, `tests/deleted_read_test.nx`, `tests/history_write_test.nx`, `tests/history_read_test.nx`, `tests/persistence_write_test.nx`, `tests/persistence_read_test.nx`, `tests/empty_database_test.nx`, `tests/open_failure_test.nx`, `tests/invalid_log_test.nx`, `tests/invalid_document_log_test.nx`

**Interfaces:**
- Consumes: `storage.replay(path) -> ReplayResult` da Task 1.
- Produces: a API pública final —
  - `noxydb.open_database(path: string) -> Database`
  - `noxydb.put(db: ref Database, key: string, value: map[string, any]) -> PutResult`
  - `noxydb.get(db: ref Database, key: string) -> LookupResult`
  - `noxydb.remove(db: ref Database, key: string) -> void`
  - `noxydb.exists(db: ref Database, key: string) -> bool`
  - `noxydb.is_open(db: ref Database) -> bool`
  - `noxydb.database_error(db: ref Database) -> string`
  - `noxydb.close_database(db: ref Database) -> void`
  - Structs `LookupResult(found: bool, value: map[string, any])`, `PutResult(success: bool, error: string)`, `DatabaseState(payloads, file_fd, path, open, error)`, `Database(state: DatabaseState)` — campos inalterados.

- [ ] **Step 1: Rodar o teste de núcleo para registrar o RED**

```powershell
$env:NOXY_EXE = 'C:\Users\estev\go\bin\noxy.exe'
& $env:NOXY_EXE tests\database_test.nx
```

Esperado: `[FAIL] put key should be found` — o `put` grava no log mas a atualização do mapa em memória se perde na cópia.

- [ ] **Step 2: Migrar as assinaturas de `noxydb/noxydb.nx`**

Substituir as declarações (mantendo os corpos como estão, exceto onde indicado nos steps 3 e 4):

```noxy
func fail_database(db: ref Database, error: string) -> void
func put(db: ref Database, key: string, value: map[string, any]) -> PutResult
func get(db: ref Database, key: string) -> LookupResult
func remove(db: ref Database, key: string) -> void
func exists(db: ref Database, key: string) -> bool
func close_database(db: ref Database) -> void
func is_open(db: ref Database) -> bool
func database_error(db: ref Database) -> string
```

`open_database`, `failed_database`, `payloads_are_valid` e `database_file` não mudam de assinatura — nenhuma delas muta um `Database` recebido. `database_file(db: Database)` permanece por valor: ela só lê campos para construir um `io.File` novo, é chamada dentro do módulo (onde a conversão contextual funciona) e não participa do padrão ler-antes-de-escrever.

- [ ] **Step 3: Corrigir as chamadas internas a `fail_database`**

Dentro de `put`, `get` e `remove`, `db` já é `ref Database`. Chamada no mesmo módulo com assinatura exata conhecida aceita a forma direta:

```noxy
        fail_database(db, "failed to write database log")
```

Nenhuma mudança textual é necessária nessas três chamadas; elas continuam como estão.

- [ ] **Step 4: Inicializar os maps declarados sem valor**

Em `noxydb/noxydb.nx:28` (dentro de `failed_database`):

```noxy
    let payloads: map[string, string] = {}
```

Em `noxydb/noxydb.nx:46` (dentro de `open_database`):

```noxy
    let payloads: map[string, string] = {}
```

Em `noxydb/noxydb.nx:97` (dentro de `get`) — esta é a que causa `runtime value metadata conflicts with static context` no caminho não-encontrado:

```noxy
    let empty: map[string, any] = {}
```

- [ ] **Step 5: Migrar os call sites dos testes**

Regra mecânica, aplicada em todos os arquivos listados em **Files**: toda chamada `noxydb.<f>(db, ...)` onde `<f>` é `put`, `get`, `remove`, `exists`, `is_open`, `database_error` ou `close_database` vira `noxydb.<f>(ref db, ...)`. O primeiro argumento é o único que ganha `ref`. `noxydb.open_database(path)` não muda.

Exemplo, `tests/database_test.nx:38-43`:

```noxy
let put_result: noxydb.PutResult = noxydb.put(ref db, "language", first)
assertions.assert_true(put_result.success, "valid document should store")
assertions.assert_string(put_result.error, "", "successful put should have no error")

let language: noxydb.LookupResult = noxydb.get(ref db, "language")
```

Acesso a campo (`db.state.payloads`) **não** muda: ler campo de uma variável local funciona igual.

- [ ] **Step 6: Migrar as funções auxiliares locais dos testes**

Três testes declaram helper que recebe `Database`. Cada um passa a receber `ref`:

`tests/database_test.nx:5`:

```noxy
func verify_removal_and_close(db: ref noxydb.Database) -> void
```

Dentro dele, todas as chamadas `noxydb.*` usam `ref db` — inclusive sendo `db` já um `ref`, porque encaminhar através de fronteira de módulo exige a forma explícita. A chamada na linha 70 permanece `verify_removal_and_close(db)`: é função local com assinatura exata conhecida, então a conversão contextual se aplica.

`tests/invalid_log_test.nx:14` e `tests/invalid_document_log_test.nx:14` declaram `assert_invalid` / `assert_invalid_payload` que recebem `path: string`, não `Database` — elas criam o `Database` localmente. Só os call sites `noxydb.is_open(db)` e `noxydb.database_error(db)` dentro delas ganham `ref db`.

- [ ] **Step 7: Adicionar `defer` de limpeza nos testes**

Em cada teste que cria um `.db`, registrar a remoção logo após definir o caminho, para o arquivo não vazar quando uma asserção falhar. Padrão, aplicado a `database_test.nx`, `document_isolation_test.nx`, `write_failure_test.nx`, `close_failure_test.nx`, `empty_database_test.nx`:

```noxy
let path: string = "tests/database_test.db"
if io.exists(path) then io.remove(path) end
defer io.remove(path)
```

A linha `if io.exists(path) then io.remove(path) end` do final do arquivo pode ser removida quando existir — o `defer` a substitui.

**Não** aplicar `defer` aos pares `persistence_write`/`persistence_read`, `deleted_write`/`deleted_read` e `history_write`/`history_read`: esses testes deixam o arquivo de propósito, para o teste de leitura correspondente consumir. O acoplamento entre eles é tratado na Task 9.

- [ ] **Step 8: Rodar o grupo de núcleo**

```powershell
.\tests\run_tests.ps1 -Group errors
& $env:NOXY_EXE tests\database_test.nx
& $env:NOXY_EXE tests\document_isolation_test.nx
& $env:NOXY_EXE tests\write_failure_test.nx
& $env:NOXY_EXE tests\close_failure_test.nx
& $env:NOXY_EXE tests\document_codec_test.nx
```

Esperado: `[PASS]` em todos.

- [ ] **Step 9: Rodar os testes de persistência na ordem correta**

```powershell
& $env:NOXY_EXE tests\persistence_write_test.nx
& $env:NOXY_EXE tests\persistence_read_test.nx
& $env:NOXY_EXE tests\deleted_write_test.nx
& $env:NOXY_EXE tests\deleted_read_test.nx
& $env:NOXY_EXE tests\history_write_test.nx
& $env:NOXY_EXE tests\history_read_test.nx
& $env:NOXY_EXE tests\empty_database_test.nx
& $env:NOXY_EXE tests\read_size_test.nx
& $env:NOXY_EXE tests\open_failure_test.nx
```

Esperado: `[PASS]` em todos. Rodar `write` antes de `read` importa — ver Task 9.

- [ ] **Step 10: Criar o teste de regressão de ler-antes-de-escrever**

Este é o teste que impede alguém de reintroduzir um parâmetro por valor sem perceber. Em vez de cravar um limite absoluto de milissegundos — que seria frágil entre máquinas e sensível ao custo de I/O do log — ele compara o laço só-escrita com o laço leitura+escrita. O invariante é que uma leitura antes de cada escrita **não multiplica** o custo da escrita.

Criar `tests/read_before_write_regression_test.nx`:

```noxy
use io
use time
use noxydb
use tests.assertions as assertions

let N: int = 2000
let doc: map[string, any] = {"v": 1}

let write_only_path: string = "tests/regression_write_only.db"
if io.exists(write_only_path) then io.remove(write_only_path) end
defer io.remove(write_only_path)
let a: noxydb.Database = noxydb.open_database(write_only_path)
let t0: int = time.now_ms()
let i: int = 0
while i < N do
    let written: noxydb.PutResult = noxydb.put(ref a, "key:" + to_str(i), doc)
    i = i + 1
end
let write_only_ms: int = time.now_ms() - t0
noxydb.close_database(ref a)

let interleaved_path: string = "tests/regression_interleaved.db"
if io.exists(interleaved_path) then io.remove(interleaved_path) end
defer io.remove(interleaved_path)
let b: noxydb.Database = noxydb.open_database(interleaved_path)
let t1: int = time.now_ms()
let j: int = 0
while j < N do
    let present: bool = noxydb.exists(ref b, "key:" + to_str(j))
    let written: noxydb.PutResult = noxydb.put(ref b, "key:" + to_str(j), doc)
    j = j + 1
end
let interleaved_ms: int = time.now_ms() - t1
noxydb.close_database(ref b)

// Se alguem trocar um parametro ref por valor, o copy-on-write marca o estado
// como compartilhado e cada put passa a clonar a keyspace inteira. A razao
// entre os dois laços explode: medido 5096ms contra 16ms em N=5000.
// O termo constante absorve jitter quando write_only_ms e muito pequeno.
let budget_ms: int = write_only_ms * 5 + 200
assertions.assert_true(interleaved_ms < budget_ms, "ler antes de escrever nao pode multiplicar o custo da escrita - " + to_str(interleaved_ms) + "ms contra orcamento de " + to_str(budget_ms) + "ms")

print("[PASS] read_before_write_regression_test (write_only=" + to_str(write_only_ms) + "ms interleaved=" + to_str(interleaved_ms) + "ms)")
```

- [ ] **Step 11: Rodar o teste de regressão**

```powershell
& $env:NOXY_EXE tests\read_before_write_regression_test.nx
```

Esperado: `[PASS] read_before_write_regression_test (...)` com os dois tempos na mesma ordem de grandeza.

Para confirmar que o teste realmente detecta a regressão, trocar temporariamente `func exists(db: ref Database, ...)` por `func exists(db: Database, ...)` em `noxydb/noxydb.nx`, rodar de novo e observar a falha. Desfazer a troca em seguida.

- [ ] **Step 12: Registrar o teste no runner**

Em `tests/run_tests.ps1`, adicionar ao array `$coreTests`:

```powershell
$coreTests = @(
    "database_test.nx",
    "document_codec_test.nx",
    "document_isolation_test.nx",
    "write_failure_test.nx",
    "close_failure_test.nx",
    "read_before_write_regression_test.nx"
)
```

- [ ] **Step 13: Commit**

```bash
git add noxydb/noxydb.nx tests/
git commit -m "feat(noxydb)!: API recebe ref Database

Noxy v0.4.0 vincula compostos por valor com copy-on-write, entao mutar db
atraves de um parametro nao-ref se perdia no frame. Toda funcao publica passa
a receber ref Database, inclusive as so-leitura: passar por valor marca o
estado como Shared e faz a escrita seguinte clonar a keyspace inteira
(medido: 5198ms contra 15ms em N=5000).

BREAKING CHANGE: chamadas passam a exigir ref explicito, como em
noxydb.put(ref db, key, value).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: `protocol.nx` — corpo não-UTF-8 e map inicializado

**Files:**
- Modify: `server/protocol.nx:33-36` (`invalid_request`), `:58-61` (`decode_api_request`)
- Modify: `tests/server_protocol_test.nx`

**Interfaces:**
- Consumes: nada das tasks anteriores.
- Produces: `protocol.decode_api_request(operation: string, body: bytes) -> ApiRequest` — assinatura inalterada, novo erro possível `"invalid JSON request"` para corpo não-UTF-8 em vez de levantar.

**Contexto:** `decode_api_request` faz `to_str(body)` na linha 60 sobre bytes vindos direto da rede. Um cliente que mande corpo não-UTF-8 faz o handler levantar. Com a stdlib isso não derruba o servidor (o `defer` interno fecha o socket), mas o cliente fica sem resposta.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar ao final de `tests/server_protocol_test.nx`, antes do `print` final:

```noxy
let non_utf8_body: bytes = hex_decode("7b226461746162617365223a22ff227d")
let non_utf8: protocol.ApiRequest = protocol.decode_api_request("open", non_utf8_body)
assertions.assert_false(non_utf8.valid, "corpo nao-UTF-8 deve ser invalido")
assertions.assert_string(non_utf8.error, "invalid JSON request", "corpo nao-UTF-8 deve ser classificado")
```

O arquivo já importa `server.protocol as protocol`, `tests.assertions as assertions` e `strings` nas linhas 1-3; nenhum import novo é necessário.

- [ ] **Step 2: Rodar para confirmar que falha**

```powershell
& $env:NOXY_EXE tests\server_protocol_test.nx
```

Esperado: `Runtime error: ... native 'to_str' failed: to_str: bytes are not valid UTF-8`

- [ ] **Step 3: Guardar a conversão em `decode_api_request`**

`server/protocol.nx` já importa `strings` na linha 2. Trocar o início de `decode_api_request`:

```noxy
func decode_api_request(operation: string, body: bytes) -> ApiRequest
    let raw: map[string, any] = {}
    if !strings.is_valid_utf8(body) then return invalid_request(operation, "invalid JSON request") end
    let body_string: string = to_str(body)
    if !json_loads(body_string, raw) then return invalid_request(operation, "invalid JSON request") end
```

- [ ] **Step 4: Inicializar o map de `invalid_request`**

Em `server/protocol.nx:34`:

```noxy
    let empty: map[string, any] = {}
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

```powershell
& $env:NOXY_EXE tests\server_protocol_test.nx
```

Esperado: `server protocol tests passed`

- [ ] **Step 6: Commit**

```bash
git add server/protocol.nx tests/server_protocol_test.nx
git commit -m "fix(protocol): corpo nao-UTF-8 vira erro de request

to_str levanta sobre bytes invalidos desde o v0.3.0, e o corpo vem direto da
rede. is_valid_utf8 passa a guardar a conversao.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: `database_worker.nx` — `ref` no slot do cache

**Files:**
- Modify: `server/database_worker.nx:20-58` (`execute_database_command`), `:60-83` (`run_database_worker`, `close_all_databases`)
- Modify: `tests/database_worker_test.nx`

**Interfaces:**
- Consumes: a API `ref` da Task 2.
- Produces:
  - `worker.execute_database_command(databases: ref map[string, noxydb.Database], data_dir: string, command: DatabaseCommand) -> protocol.ApiResponse`
  - `worker.close_all_databases(databases: ref map[string, noxydb.Database]) -> void`
  - `worker.run_database_worker(commands: chan any, done: chan any, data_dir: string, execute: func, close_all: func) -> void` — **ganha o parâmetro `done` como segundo argumento**, usado no handshake de encerramento da Task 6.
  - `worker.DatabaseCommand(operation: string, database: string, key: string, value: map[string, any], response: any)`
  - `worker.new_database_command(operation, database, key, value) -> DatabaseCommand`

- [ ] **Step 1: Rodar o teste do worker para registrar o RED**

```powershell
& $env:NOXY_EXE tests\database_worker_test.nx
```

Esperado: `[FAIL] get should return stored document`

- [ ] **Step 2: Ligar ao slot do cache em `execute_database_command`**

Substituir o corpo de `execute_database_command` por:

```noxy
func execute_database_command(databases: ref map[string, noxydb.Database], data_dir: string, command: DatabaseCommand) -> protocol.ApiResponse
    if command.operation == "open" then
        if has_key(databases, command.database) then
            let cached: ref noxydb.Database = ref databases[command.database]
            if !noxydb.is_open(ref cached) then return protocol.api_error(500, noxydb.database_error(ref cached)) end
        else
            let opened: noxydb.Database = noxydb.open_database(database_path(data_dir, command.database))
            if !noxydb.is_open(ref opened) then return protocol.api_error(500, noxydb.database_error(ref opened)) end
            databases[command.database] = opened
        end
        return protocol.api_success({"success": true, "error": ""})
    end
    if !has_key(databases, command.database) then return protocol.api_error(409, "database is not open") end

    let db: ref noxydb.Database = ref databases[command.database]
    if !noxydb.is_open(ref db) then return protocol.api_error(500, noxydb.database_error(ref db)) end
    if command.operation == "put" then
        let result: noxydb.PutResult = noxydb.put(ref db, command.key, command.value)
        if !result.success then return protocol.api_error(500, result.error) end
        return protocol.api_success({"success": true, "error": ""})
    end
    if command.operation == "get" then
        let result: noxydb.LookupResult = noxydb.get(ref db, command.key)
        if noxydb.database_error(ref db) != "" then return protocol.api_error(500, noxydb.database_error(ref db)) end
        return protocol.api_success({"found": result.found, "value": result.value, "error": ""})
    end
    if command.operation == "exists" then
        return protocol.api_success({"exists": noxydb.exists(ref db, command.key), "error": ""})
    end
    if command.operation == "remove" then
        noxydb.remove(ref db, command.key)
        if noxydb.database_error(ref db) != "" then return protocol.api_error(500, noxydb.database_error(ref db)) end
        return protocol.api_success({"success": true, "error": ""})
    end
    if command.operation == "close" then
        return protocol.api_success({"success": true, "error": ""})
    end
    return protocol.api_error(404, "unknown operation")
end
```

Note que `databases[command.database] = opened` guarda o valor **antes** de qualquer `ref` para dentro dele — a spec de `ref` alerta que criar `ref` para dentro de um contêiner fixa a identidade na criação, então a ordem importa.

- [ ] **Step 3: Ligar ao slot em `close_all_databases`**

```noxy
func close_all_databases(databases: ref map[string, noxydb.Database]) -> void
    let names: string[] = keys(databases)
    let index: int = 0
    while index < length(names) do
        let db: ref noxydb.Database = ref databases[names[index]]
        noxydb.close_database(ref db)
        index = index + 1
    end
end
```

- [ ] **Step 4: Adicionar o canal de handshake em `run_database_worker`**

```noxy
func run_database_worker(commands: chan any, done: chan any, data_dir: string, execute: func, close_all: func) -> void
    let databases: map[string, noxydb.Database] = {}
    let running: bool = true
    while running do
        let raw: any = chan_recv(commands)
        if raw == null && chan_is_closed(commands) then
            running = false
        else
            let command: DatabaseCommand = raw
            let response: protocol.ApiResponse = execute(ref databases, data_dir, command)
            chan_send(command.response, response)
        end
    end
    close_all(ref databases)
    chan_send(done, true)
end
```

O `chan_send(done, true)` é o que permite o `main` esperar o fechamento físico antes de sair. Sem ele o processo sai antes de o worker fechar os `.db`.

- [ ] **Step 5: Inicializar o map do teste**

Em `tests/database_worker_test.nx:14`:

```noxy
let databases: map[string, noxydb.Database] = {}
```

- [ ] **Step 6: Adicionar limpeza garantida ao teste**

Logo após a linha 12 (`let second_path: ...`), registrar a limpeza. Como `defer` executa na saída do frame e o teste é de topo, ele cobre também a saída por falha de asserção:

```noxy
defer io.remove(data_dir)
defer io.remove(second_path)
defer io.remove(first_path)
```

A ordem LIFO garante que os arquivos saem antes do diretório. As linhas 35-37 (`first_removed`, `second_removed`, `directory_removed`) e as asserções 50-52 que dependem delas devem ser removidas, porque a limpeza deixa de ser o objeto do teste.

- [ ] **Step 7: Rodar o teste e confirmar que passa**

```powershell
& $env:NOXY_EXE tests\database_worker_test.nx
```

Esperado: `database worker tests passed`

- [ ] **Step 8: Criar o teste de supervisão e handshake do worker**

Dois invariantes que a Task 6 vai depender: o worker confirma o fechamento antes de sair, e uma falha dele é observável em vez de silenciosa. `run_database_worker` recebe `execute` e `close_all` como `func`, o que permite injetar um `execute` que levanta.

Criar `tests/worker_supervision_test.nx`:

```noxy
use noxydb
use server.protocol as protocol
use server.database_worker as worker
use tests.assertions as assertions

func exploding_execute(databases: ref map[string, noxydb.Database], data_dir: string, command: worker.DatabaseCommand) -> protocol.ApiResponse
    let boom: int = to_int("nao eh numero")
    return protocol.api_success({"success": true, "error": ""})
end

func noop_close(databases: ref map[string, noxydb.Database]) -> void
end

// 1. Encerramento normal confirma pelo canal de handshake.
let ok_commands: chan any = make_chan(4)
let ok_done: chan any = make_chan(1)
let ok_task: any = spawn_task(worker.run_database_worker, ok_commands, ok_done, "tests", worker.execute_database_command, worker.close_all_databases)
chan_close(ok_commands)
let ack: any = chan_recv(ok_done)
assertions.assert_true(ack == true, "worker deve confirmar o fechamento antes de sair")
let ok_outcome: any = task_await(ok_task, 5000)
assertions.assert_string(to_str(ok_outcome["status"]), "ok", "encerramento normal deve terminar em ok")

// 2. Um worker que levanta e observavel pelo handle, nao silencioso.
let bad_commands: chan any = make_chan(4)
let bad_done: chan any = make_chan(1)
let bad_task: any = spawn_task(worker.run_database_worker, bad_commands, bad_done, "tests", exploding_execute, noop_close)
let reply: any = make_chan(0)
chan_send(bad_commands, worker.DatabaseCommand("get", "db", "k", {}, reply))
let bad_outcome: any = task_await(bad_task, 5000)
assertions.assert_string(to_str(bad_outcome["status"]), "error", "worker que levanta deve ser observavel")
let failure: map[string, any] = bad_outcome["error"]
assertions.assert_string(to_str(failure["kind"]), "runtime", "falha deve ser classificada como runtime")

print("[PASS] worker_supervision_test")
```

- [ ] **Step 9: Rodar o teste de supervisão**

```powershell
& $env:NOXY_EXE tests\worker_supervision_test.nx
```

Esperado: `[PASS] worker_supervision_test`. Se o primeiro bloco travar em `chan_recv(ok_done)`, o `chan_send(done, true)` do step 4 não foi adicionado.

- [ ] **Step 10: Registrar o teste no runner**

Em `tests/run_tests.ps1`, no array `$serverTests`:

```powershell
$serverTests = @(
    "server_protocol_test.nx",
    "database_worker_test.nx",
    "worker_supervision_test.nx"
)
```

`api_test.nx` entra nesse mesmo array na Task 5.

- [ ] **Step 11: Commit**

```bash
git add server/database_worker.nx tests/database_worker_test.nx tests/worker_supervision_test.nx tests/run_tests.ps1
git commit -m "fix(worker): muta a entrada do cache pelo slot

Ler de um map passou a copiar no v0.4.0, entao a mutacao do Database em cache
se perdia. O acesso passa por ref databases[nome]. run_database_worker ganha
um canal done para confirmar o fechamento fisico ao encerrar.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: `server/api.nx` — camada HTTP sobre a stdlib

**Files:**
- Create: `server/api.nx`
- Delete: `server/http_transport.nx`
- Create: `tests/api_test.nx`
- Delete: `tests/http_transport_test.nx`
- Modify: `tests/run_tests.ps1:57-61`

**Interfaces:**
- Consumes: `protocol.decode_api_request`, `protocol.api_success`, `protocol.api_error`, `protocol.ApiResponse` (Task 3); `worker.DatabaseCommand` (Task 4).
- Produces:
  - `api.operation_for(method: string, path: string) -> string`
  - `api.escaped_log_field(value: string) -> string`
  - `api.quoted_log_value(value: string) -> string`
  - `api.build_activity_line(method: string, path: string, body: bytes, status: int, duration_ms: int, timestamp: string) -> string` — **assinatura nova**: recebe método, caminho e corpo soltos em vez do antigo `HttpReadResult`, que deixou de existir.
  - `api.json_response(response: protocol.ApiResponse) -> HttpResponse`
  - `api.route(method: string, path: string, body: bytes, commands: chan any, shutdown_enabled: bool, shutdown_signal: chan any) -> protocol.ApiResponse`

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/api_test.nx`:

```noxy
use server.api as api
use tests.assertions as assertions

// operation_for
assertions.assert_string(api.operation_for("POST", "/v1/put"), "put", "POST /v1/put mapeia para put")
assertions.assert_string(api.operation_for("POST", "/v1/get"), "get", "POST /v1/get mapeia para get")
assertions.assert_string(api.operation_for("GET", "/v1/put"), "", "metodo errado nao mapeia")
assertions.assert_string(api.operation_for("POST", "/v1/nope"), "", "rota desconhecida nao mapeia")

// escaped_log_field: este e o caso que a semantica antiga de substring quebrava
assertions.assert_string(api.escaped_log_field("abc"), "abc", "campo simples nao perde caractere")
assertions.assert_string(api.escaped_log_field("a\nb"), "a\\nb", "quebra de linha e escapada")
assertions.assert_string(api.escaped_log_field("a\tb"), "a\\tb", "tab e escapado")

// quoted_log_value mantem as aspas
assertions.assert_string(api.quoted_log_value("user:1"), "\"user:1\"", "valor citado mantem aspas")

// linha de atividade
let line: string = api.build_activity_line("POST", "/v1/put", to_bytes("{\"database\":\"usuarios\",\"key\":\"user:1\",\"value\":{}}"), 200, 3, "2026-08-16T14:32:11")
assertions.assert_string(line, "2026-08-16T14:32:11 POST /v1/put database=usuarios key=\"user:1\" status=200 duration_ms=3", "linha de atividade completa")

let unknown: string = api.build_activity_line("", "", to_bytes(""), 400, 0, "2026-08-16T14:32:12")
assertions.assert_string(unknown, "2026-08-16T14:32:12 UNKNOWN - status=400 duration_ms=0", "request nao parseada usa placeholders")

print("[PASS] api_test")
```

- [ ] **Step 2: Rodar para confirmar que falha**

```powershell
& $env:NOXY_EXE tests\api_test.nx
```

Esperado: erro de módulo não encontrado para `server.api`.

- [ ] **Step 3: Criar `server/api.nx`**

```noxy
use json
use strings
use time
use http_parser select *
use http_server select *
use server.protocol as protocol
use server.database_worker as worker

func operation_for(method: string, path: string) -> string
    if method == "POST" && path == "/v1/open" then return "open" end
    if method == "POST" && path == "/v1/put" then return "put" end
    if method == "POST" && path == "/v1/get" then return "get" end
    if method == "POST" && path == "/v1/exists" then return "exists" end
    if method == "POST" && path == "/v1/remove" then return "remove" end
    if method == "POST" && path == "/v1/close" then return "close" end
    return ""
end

func known_path(path: string) -> bool
    if path == "/v1/health" then return true end
    if path == "/v1/open" then return true end
    if path == "/v1/put" then return true end
    if path == "/v1/get" then return true end
    if path == "/v1/exists" then return true end
    if path == "/v1/remove" then return true end
    if path == "/v1/close" then return true end
    return false
end

func quoted_log_value(value: string) -> string
    let encoded: json.EncodeResult = json.dumps_result(value)
    if encoded.success then return encoded.data end
    return "\"<invalid>\""
end

// Remove apenas as aspas externas do JSON. O terceiro argumento de substring
// e um indice final EXCLUSIVO: length - 1 descarta a aspa final.
func escaped_log_field(value: string) -> string
    let encoded: json.EncodeResult = json.dumps_result(value)
    if !encoded.success || length(encoded.data) < 2 then return "<invalid>" end
    return strings.substring(encoded.data, 1, length(encoded.data) - 1)
end

func build_activity_line(method: string, path: string, body: bytes, status: int, duration_ms: int, timestamp: string) -> string
    let shown_method: string = method
    let shown_path: string = path
    if shown_method == "" then shown_method = "UNKNOWN" end
    if shown_path == "" then shown_path = "-" end

    let line: string = timestamp + " " + escaped_log_field(shown_method) + " " + escaped_log_field(shown_path)
    let operation: string = operation_for(method, path)
    if operation != "" then
        let decoded: protocol.ApiRequest = protocol.decode_api_request(operation, body)
        if decoded.valid then
            line = line + " database=" + decoded.database
            if operation == "put" || operation == "get" || operation == "exists" || operation == "remove" then
                line = line + " key=" + quoted_log_value(decoded.key)
            end
        end
    end

    let elapsed_ms: int = duration_ms
    if elapsed_ms < 0 then elapsed_ms = 0 end
    return line + " status=" + to_str(status) + " duration_ms=" + to_str(elapsed_ms)
end

func reason_phrase(status: int) -> string
    if status == 200 then return "OK" end
    if status == 400 then return "Bad Request" end
    if status == 404 then return "Not Found" end
    if status == 405 then return "Method Not Allowed" end
    if status == 409 then return "Conflict" end
    return "Internal Server Error"
end

// Preserva o corpo JSON da API mesmo em status de erro. response_json devolve
// 200 com application/json; sobrescrever o status mantem o Content-Type.
func json_response(response: protocol.ApiResponse) -> HttpResponse
    let built: HttpResponse = response_json(response.json)
    built.status_code = response.status
    built.status_text = reason_phrase(response.status)
    return built
end

func route(method: string, path: string, body: bytes, commands: chan any, shutdown_enabled: bool, shutdown_signal: chan any) -> protocol.ApiResponse
    if method == "GET" && path == "/v1/health" then
        return protocol.api_success({"success": true, "status": "ok"})
    end
    if path == "/v1/shutdown" then
        if !shutdown_enabled then return protocol.api_error(404, "not found") end
        if method != "POST" then return protocol.api_error(405, "method not allowed") end
        chan_send(shutdown_signal, true)
        return protocol.api_success({"success": true, "error": ""})
    end
    let operation: string = operation_for(method, path)
    if operation == "" then
        if known_path(path) then return protocol.api_error(405, "method not allowed") end
        return protocol.api_error(404, "not found")
    end
    let decoded: protocol.ApiRequest = protocol.decode_api_request(operation, body)
    if !decoded.valid then return protocol.api_error(400, decoded.error) end
    let reply: any = make_chan(0)
    let command: worker.DatabaseCommand = worker.DatabaseCommand(decoded.operation, decoded.database, decoded.key, decoded.value, reply)
    chan_send(commands, command)
    let response: protocol.ApiResponse = chan_recv(reply)
    chan_close(reply)
    return response
end
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

```powershell
& $env:NOXY_EXE tests\api_test.nx
```

Esperado: `[PASS] api_test`. Se `escaped_log_field` falhar devolvendo um caractere a menos, a linha do `substring` ficou com `- 2` em vez de `- 1`.

- [ ] **Step 5: Apagar o transporte artesanal e o teste antigo**

```bash
git rm server/http_transport.nx tests/http_transport_test.nx
```

- [ ] **Step 6: Atualizar o runner**

Em `tests/run_tests.ps1`, no array `$serverTests`, substituir `http_transport_test.nx` por `api_test.nx`, preservando a entrada `worker_supervision_test.nx` que a Task 4 adicionou:

```powershell
$serverTests = @(
    "server_protocol_test.nx",
    "database_worker_test.nx",
    "worker_supervision_test.nx",
    "api_test.nx"
)
```

- [ ] **Step 7: Rodar o grupo do servidor**

```powershell
.\tests\run_tests.ps1 -Group server
```

Esperado: `All NoxyDB tests passed (4 files).`

- [ ] **Step 8: Commit**

```bash
git add server/api.nx tests/api_test.nx tests/run_tests.ps1
git commit -m "refactor(server)!: troca o transporte artesanal pela stdlib http_server

As 328 linhas de http_transport.nx existiam por causa de limitacoes que a
linguagem resolveu: net_select inseguro concorrente, polling que consumia
byte, timeout zero virando 1ms e ausencia de framing incremental. Some o
semaforo de rede, o recv byte a byte e a matematica de deadline.

Corrige tambem escaped_log_field, que usava substring com o terceiro
argumento como comprimento; ele passou a ser indice final exclusivo no v0.3.0.

BREAKING CHANGE: erros de transporte passam a usar 408/413/414/431/501/505
com corpo text/plain, em vez de 400 com corpo JSON.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: `noxydb_server.nx` — bind, sinais, shutdown e supervisão

**Files:**
- Modify: `server/noxydb_server.nx` (arquivo inteiro a partir da linha 1)

**Interfaces:**
- Consumes: `api.route`, `api.json_response`, `api.build_activity_line` (Task 5); `worker.run_database_worker` com o parâmetro `done` (Task 4).
- Produces: executável de servidor com as opções `--data-dir <path>`, `--port <1-65535>` e `--enable-shutdown`.

**Contexto:** `serve` bloqueia, então `stop_server` tem que vir de outra rotina. O handler roda em rotina spawnada pelo `serve`, e a rotina de sinais é independente — as duas satisfazem isso. O `main` precisa esperar o handshake do worker antes de sair, senão o processo termina antes do fechamento físico dos `.db`.

- [ ] **Step 1: Substituir `server/noxydb_server.nx`**

Preservar `decimal_token`, `parse_port` e a struct `ServerOptions` como estão; trocar `parse_options` e todo o bloco de topo:

```noxy
use io
use sys
use time
use strings
use http_parser select *
use http_server select *
use server.protocol as protocol
use server.database_worker as worker
use server.api as api

struct ServerOptions
    valid: bool
    data_dir: string
    port: int
    enable_shutdown: bool
    error: string
end

func decimal_token(value: string) -> bool
    if length(value) == 0 then return false end
    let index: int = 0
    while index < length(value) do
        if !strings.is_digit(strings.char_at(value, index)) then return false end
        index = index + 1
    end
    return true
end

func parse_port(value: string) -> ServerOptions
    if !decimal_token(value) then return ServerOptions(false, "", 0, false, "port must be a decimal number") end
    let first_significant: int = 0
    while first_significant < length(value) && strings.char_at(value, first_significant) == "0" do
        first_significant = first_significant + 1
    end
    let normalized: string = "0"
    if first_significant < length(value) then normalized = strings.substring(value, first_significant, length(value)) end
    if length(normalized) > 5 then return ServerOptions(false, "", 0, false, "port must be between 1 and 65535") end
    let port: int = to_int(normalized)
    if port < 1 || port > 65535 then return ServerOptions(false, "", 0, false, "port must be between 1 and 65535") end
    return ServerOptions(true, "", port, false, "")
end

func parse_options(args: string[]) -> ServerOptions
    let data_dir: string = ""
    let port: int = 8765
    let enable_shutdown: bool = false
    let index: int = 2
    while index < length(args) do
        if args[index] == "--data-dir" && index + 1 < length(args) then
            data_dir = args[index + 1]
            index = index + 2
        elif args[index] == "--enable-shutdown" then
            enable_shutdown = true
            index = index + 1
        elif args[index] == "--port" && index + 1 < length(args) then
            let parsed_port: ServerOptions = parse_port(args[index + 1])
            if !parsed_port.valid then return parsed_port end
            port = parsed_port.port
            index = index + 2
        else
            return ServerOptions(false, "", 0, false, "usage: noxy noxydb_server.nx --data-dir <path> [--port <1-65535>] [--enable-shutdown]")
        end
    end
    if data_dir == "" then return ServerOptions(false, "", 0, false, "--data-dir is required") end
    if port < 1 || port > 65535 then return ServerOptions(false, "", 0, false, "port must be between 1 and 65535") end
    return ServerOptions(true, data_dir, port, enable_shutdown, "")
end

let options: ServerOptions = parse_options(sys.argv())
if !options.valid then
    print(options.error)
    sys.exit(2)
end
if io.exists(options.data_dir) then
    if !io.stat(options.data_dir).is_dir then
        print("data path is not a directory")
        sys.exit(2)
    end
elif !io.mkdir(options.data_dir) then
    print("failed to create data directory")
    sys.exit(2)
end

let commands: chan any = make_chan(64)
let worker_done: chan any = make_chan(0)
let shutdown_signal: chan any = make_chan(1)
let signals: chan any = make_chan(1)
let server: HttpServer = new_server("127.0.0.1", options.port)
server.max_body_bytes = 1048576

func handler(req: HttpRequest) -> HttpResponse
    let started_at: int = time.now_ms()
    let response: protocol.ApiResponse = api.route(req.method, req.path, req.body, commands, options.enable_shutdown, shutdown_signal)
    let built: HttpResponse = api.json_response(response)
    let timestamp: string = time.format_custom(time.now_datetime(), "%Y-%m-%dT%H:%M:%S")
    print(api.build_activity_line(req.method, req.path, req.body, response.status, time.now_ms() - started_at, timestamp))
    return built
end

// Espera qualquer gatilho de encerramento e para o listener. Roda em rotina
// separada porque serve() bloqueia ate o listener fechar.
func wait_for_shutdown() -> void
    let reason: any = chan_recv(shutdown_signal)
    stop_server(ref server)
end

func wait_for_signal() -> void
    let received: any = chan_recv(signals)
    print("received signal " + to_str(received) + ", shutting down")
    chan_send(shutdown_signal, true)
end

let worker_task: any = spawn_task(worker.run_database_worker, commands, worker_done, options.data_dir, worker.execute_database_command, worker.close_all_databases)
spawn(wait_for_shutdown)
if sys.signal_notify(signals) then
    spawn(wait_for_signal)
end

print("NoxyDB server listening on http://127.0.0.1:" + to_str(options.port))
serve(ref server, handler)

chan_close(commands)
let worker_ack: any = chan_recv(worker_done)
let outcome: any = task_await(worker_task, 0)
if outcome["status"] == "error" then
    let failure: map[string, any] = outcome["error"]
    print("database worker failed: " + to_str(failure["message"]))
    sys.exit(1)
end
print("NoxyDB server stopped")
```

Três detalhes que fazem esse arquivo funcionar e não são óbvios:

- `wait_for_shutdown` roda em rotina própria porque `serve` bloqueia até o listener fechar; `stop_server` chamado de dentro do `serve` nunca aconteceria.
- `chan_recv(worker_done)` vem **depois** de `chan_close(commands)`. Invertido, trava: o worker só confirma depois de ver o canal fechado.
- `task_await(worker_task, 0)` no fim é poll imediato, não espera. Nesse ponto o worker já confirmou pelo handshake, então o desfecho é terminal.

- [ ] **Step 2: Verificar que o servidor sobe e responde**

Em um terminal:

```powershell
$env:NOXY_EXE = 'C:\Users\estev\go\bin\noxy.exe'
& $env:NOXY_EXE server\noxydb_server.nx --data-dir .\data --port 8765
```

Em outro:

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:8765/v1/health" -UseBasicParsing | Select-Object -ExpandProperty Content
```

Esperado: `{"success":true,"status":"ok"}` e uma linha de atividade no console do servidor.

- [ ] **Step 3: Verificar que `/v1/shutdown` responde 404 sem a flag**

Com o servidor do step anterior ainda rodando:

```powershell
try { Invoke-WebRequest -Uri "http://127.0.0.1:8765/v1/shutdown" -Method POST -Body "{}" -UseBasicParsing } catch { [int]$_.Exception.Response.StatusCode }
```

Esperado: `404`.

- [ ] **Step 4: Verificar Ctrl-C encerrando limpo**

No terminal do servidor, pressionar Ctrl-C.

Esperado, em ordem: `received signal 2, shutting down` e depois `NoxyDB server stopped`. O processo termina sozinho. Se o processo travar após a primeira linha, o handshake do `worker_done` não está fechando — confira a Task 4 step 4.

- [ ] **Step 5: Verificar a rota com a flag ligada**

```powershell
& $env:NOXY_EXE server\noxydb_server.nx --data-dir .\data --port 8765 --enable-shutdown
```

Em outro terminal:

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:8765/v1/shutdown" -Method POST -Body "{}" -UseBasicParsing | Select-Object -ExpandProperty Content
```

Esperado: `{"success":true,"error":""}` e o servidor imprimindo `NoxyDB server stopped` antes de sair.

- [ ] **Step 6: Commit**

```bash
git add server/noxydb_server.nx
git commit -m "feat(server): encerramento limpo por sinal e por rota

sys.signal_notify entrega SIGINT/SIGTERM, e stop_server encerra o accept loop.
O worker confirma o fechamento fisico dos .db por um canal de handshake antes
de o processo sair -- sem ele o processo terminava antes de fechar nada.
A rota /v1/shutdown so existe com --enable-shutdown; sem a flag, 404.

O worker sobe com spawn_task: se ele morrer, o servidor reporta a falha em vez
de deixar todo request pendurado em chan_recv.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: Cliente Python — erros `text/plain` e `shutdown()`

**Files:**
- Modify: `python/src/noxydb/client.py:142-164` (`_request`), e a classe `NoxyDBClient`
- Modify: `python/tests/test_client.py`

**Interfaces:**
- Consumes: o contrato HTTP da Task 5.
- Produces: `NoxyDBClient.shutdown() -> None`; `_request` levantando `NoxyDBServerError(status, reason)` para corpo `text/plain`.

**Contexto:** `_request` exige corpo JSON em toda resposta de erro. Os códigos de transporte da stdlib (408, 413, 414, 431, 501, 505) vêm com `text/plain`, então hoje virariam `NoxyDBConnectionError("invalid error response")`, escondendo o status real.

- [ ] **Step 1: Escrever os testes que falham**

`test_client.py` já tem um servidor HTTP real programável, `_RecordingServer`: cada teste enfileira `(status, corpo)` em `self.server.responses`, e um corpo `bytes` é enviado cru. Nenhum import novo é necessário — `NoxyDBServerError` e `NoxyDBConnectionError` já estão importados nas linhas 11-20.

Acrescentar dentro da classe `ClientTests`:

```python
    def test_plain_text_error_body_becomes_server_error(self) -> None:
        # Erros de transporte do http_server da stdlib chegam como texto cru,
        # nao como o envelope JSON da API.
        self.server.responses.append((413, b"Content Too Large"))
        with self.assertRaises(NoxyDBServerError) as captured:
            self.client.health()
        self.assertEqual(captured.exception.status, 413)
        self.assertEqual(str(captured.exception), "Content Too Large")

    def test_timeout_status_is_preserved(self) -> None:
        self.server.responses.append((408, b"Request Timeout"))
        with self.assertRaises(NoxyDBServerError) as captured:
            self.client.health()
        self.assertEqual(captured.exception.status, 408)

    def test_empty_error_body_is_a_connection_error(self) -> None:
        self.server.responses.append((500, b""))
        with self.assertRaises(NoxyDBConnectionError):
            self.client.health()
```

- [ ] **Step 2: Rodar para confirmar que falha**

```powershell
$env:PYTHONPATH = "python\src"
python -m unittest python.tests.test_client.ClientTests.test_plain_text_error_body_becomes_server_error -v
```

Esperado: FAIL com `NoxyDBConnectionError` no lugar de `NoxyDBServerError`.

- [ ] **Step 3: Aceitar corpo de erro não-JSON em `_request`**

Substituir o bloco `except urllib.error.HTTPError as error:` de `client.py` por:

```python
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
            try:
                decoded_error = json.loads(
                    raw_error.decode("utf-8"),
                    parse_constant=_reject_json_constant,
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                # Erros de transporte vem do http_server da stdlib como
                # text/plain, com a razao no corpo. Nao ha JSON a decodificar.
                reason = raw_error.decode("utf-8", errors="replace").strip()
                if not reason:
                    raise NoxyDBConnectionError("invalid error response") from error
                raise NoxyDBServerError(error.code, reason) from error
            if (
                not isinstance(decoded_error, dict)
                or decoded_error.get("success") is not False
                or not isinstance(decoded_error.get("error"), str)
                or decoded_error["error"] == ""
            ):
                raise NoxyDBConnectionError("invalid error response") from error
            raise NoxyDBServerError(error.code, decoded_error["error"]) from error
```

- [ ] **Step 4: Rodar os dois testes novos**

```powershell
python -m unittest python.tests.test_client.ClientTests.test_plain_text_error_body_becomes_server_error python.tests.test_client.ClientTests.test_empty_error_body_is_a_connection_error -v
```

Esperado: `OK` nos dois.

- [ ] **Step 5: Escrever o teste de `shutdown()`**

Acrescentar a `ClientTests`:

```python
    def test_shutdown_posts_to_the_shutdown_route(self) -> None:
        self.server.responses.append((200, {"success": True, "error": ""}))
        self.client.shutdown()
        request = self.server.requests[-1]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/v1/shutdown")

    def test_shutdown_reports_a_missing_route(self) -> None:
        # Servidor iniciado sem --enable-shutdown responde 404 nesta rota.
        self.server.responses.append((404, {"success": False, "error": "not found"}))
        with self.assertRaises(NoxyDBServerError) as captured:
            self.client.shutdown()
        self.assertEqual(captured.exception.status, 404)
```

- [ ] **Step 6: Implementar `shutdown()`**

Adicionar à classe `NoxyDBClient`, logo após `open_database`:

```python
    def shutdown(self) -> None:
        """Pede o encerramento do servidor.

        So funciona contra um servidor iniciado com --enable-shutdown; sem a
        flag a rota nao existe e o servidor responde 404.
        """
        response = self._request("/v1/shutdown", {})
        success, error = _require_operation_result(response)
        if not success:
            raise NoxyDBServerError(200, error)
```

- [ ] **Step 7: Rodar a suíte do cliente**

```powershell
.\tests\run_tests.ps1 -Group python
```

Esperado: `OK` e `All Python client tests passed.`

- [ ] **Step 8: Commit**

```bash
git add python/src/noxydb/client.py python/tests/test_client.py
git commit -m "feat(client): aceita erro text/plain e expoe shutdown()

Os codigos de transporte do http_server da stdlib (408, 413, 414, 431, 501,
505) vem com corpo text/plain. O cliente exigia JSON e os colapsava em
NoxyDBConnectionError, escondendo o status real.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: Testes de integração

**Files:**
- Modify: `python/tests/test_integration.py:191-211` e o harness de subida do servidor

**Interfaces:**
- Consumes: o servidor da Task 6 e o cliente da Task 7.

- [ ] **Step 1: Parametrizar a subida do servidor**

`IntegrationTests._start_server` (linhas 40-54) monta a linha de comando fixa. Adicionar um atributo de classe e usá-lo, para uma segunda classe poder ligar a flag sem duplicar o harness.

Logo abaixo de `class IntegrationTests(unittest.TestCase):` e da linha `process: subprocess.Popen[bytes] | None = None`, adicionar:

```python
    extra_args: list[str] = []
```

E em `_start_server`, trocar a lista de argumentos por:

```python
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
```

- [ ] **Step 2: Ajustar o teste de request acima de 1 MiB**

O módulo já tem `_raw_http(parts)` (linhas 92-107), que abre a conexão, envia cada parte, sinaliza fim de escrita e lê tudo. O teste atual já o usa; só mudam as asserções — de 400 com JSON para 413 com `text/plain`:

```python
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
```

- [ ] **Step 3: Substituir o teste de surplus**

`test_fragmented_surplus_is_rejected_before_routing` perde a premissa: a stdlib **descarta** bytes além do corpo declarado em vez de recusá-los, porque fecha a conexão após uma resposta. Trocar a asserção em silêncio esconderia essa perda de rigor atrás de um teste verde, então o método é substituído por um que fixa o comportamento novo e diz por quê:

Mantendo exatamente a mesma forma de três partes do teste original (a terceira é uma requisição pipelined), só invertendo o resultado esperado:

```python
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
```

A última asserção é o inverso exato da original (`assertFalse` → `assertTrue`): antes o roteamento era suprimido, agora ele acontece e o banco é criado.

- [ ] **Step 4: Ajustar o teste de cliente incompleto**

O teste atual usa `stalled.settimeout(2.5)` e espera `400 Bad Request` com o JSON `"error":"request read timeout"`, produzidos pelo deadline artesanal de 1.000 ms. O `header_timeout_ms` default da stdlib é 5.000 ms, então **o socket expiraria antes da resposta** e o teste falharia por `socket.timeout` em vez de asserção. Substituir o método inteiro por:

```python
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
```

A asserção que importa continua sendo a do meio: o cliente saudável responde em 0,5 s enquanto o outro está parado. É ela que prova que remover o semáforo de rede não serializou as conexões.

`_raw_http` usa `timeout=5` fixo na linha 94, e nenhum teste que passa por ele depende de um timeout de servidor, então esse valor não muda.

- [ ] **Step 5: Adicionar teste da rota ausente sem a flag**

Dentro de `IntegrationTests`, que sobe o servidor **sem** `--enable-shutdown`:

```python
    def test_shutdown_route_is_absent_without_the_flag(self) -> None:
        with self.assertRaises(NoxyDBServerError) as captured:
            self.client.shutdown()
        self.assertEqual(captured.exception.status, 404)
```

- [ ] **Step 5b: Adicionar a classe que exercita o encerramento**

No fim de `python/tests/test_integration.py`, uma classe própria com a flag ligada. Ela herda o harness e adiciona só o teste de encerramento; como derruba o servidor de propósito, precisa ficar isolada dos demais:

```python
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


class ShutdownRouteTests(IntegrationTests):
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
```

**Atenção:** herdar de `IntegrationTests` faz o unittest reexecutar todos os testes da classe base sob a flag. Isso é aceitável e até útil — prova que ligar `--enable-shutdown` não altera nenhum outro comportamento — mas dobra o tempo da suíte de integração. Se o tempo incomodar, extrair o harness (linhas 15-107) para uma classe `_ServerHarness(unittest.TestCase)` sem métodos de teste e fazer `IntegrationTests(_ServerHarness)` e `ShutdownRouteTests(_ServerHarness)` herdarem dela.

Note também que `_stop_server` chama `process.terminate()`, que no Windows é `TerminateProcess` e **não** entrega `SIGTERM`. O caminho de sinal continua verificado manualmente na Task 6 step 4, não por este teste.

- [ ] **Step 6: Rodar a suíte de integração**

```powershell
.\tests\run_tests.ps1 -Group integration
```

Esperado: `OK` e `All NoxyDB integration tests passed.`

Prestar atenção especial a `test_concurrent_clients_are_serialized_without_lost_documents`: ele é a regressão que prova que remover o semáforo de rede não reintroduziu a corrida. Se ele falhar, o problema está na Task 5 ou 6, não aqui.

- [ ] **Step 7: Commit**

```bash
git add python/tests/test_integration.py
git commit -m "test(integration): acompanha o contrato HTTP da stdlib

413 para corpo acima do limite, 408 para cliente parado, e o surplus passa a
ser descartado em vez de rejeitado -- comportamento novo fixado por teste
proprio em vez de asserção trocada em silencio. Cobre encerramento por rota
e ausencia da rota sem a flag.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: Exemplos e acoplamento de ordem dos testes

**Files:**
- Modify: `examples/documents.nx`, `examples/cadastro_usuarios.nx`
- Modify: `tests/run_tests.ps1:42-50`

**Interfaces:**
- Consumes: a API `ref` da Task 2.

- [ ] **Step 1: Rodar o exemplo para registrar o RED**

```powershell
& $env:NOXY_EXE examples\documents.nx
```

Esperado: falha ou comportamento errado — os `put` não se refletem nos `get`.

- [ ] **Step 2: Migrar `examples/documents.nx`**

Aplicar a mesma regra da Task 2 step 5: toda chamada `noxydb.<f>(db, ...)` com `<f>` em `put`, `get`, `remove`, `exists`, `is_open`, `database_error`, `close_database` recebe `ref` no primeiro argumento.

- [ ] **Step 3: Migrar `examples/cadastro_usuarios.nx`**

Mesma regra. Este arquivo é maior; varrer com:

```powershell
Select-String -Path examples\cadastro_usuarios.nx -Pattern 'noxydb\.(put|get|remove|exists|is_open|database_error|close_database)\('
```

Cada ocorrência listada precisa de `ref` no primeiro argumento. Funções locais do exemplo que recebam `noxydb.Database` passam a receber `ref noxydb.Database`.

- [ ] **Step 4: Rodar os dois exemplos**

```powershell
& $env:NOXY_EXE examples\documents.nx
```

Esperado: a saída completa do walkthrough, sem erro. `examples/cadastro_usuarios.nx` é interativo; validar que ele inicia e lista o menu, e sair.

- [ ] **Step 5: Tornar explícito o acoplamento de ordem nos testes de persistência**

`persistence_read_test.nx` consome o arquivo deixado por `persistence_write_test.nx`, e o mesmo vale para os pares `deleted_*` e `history_*`. A ordem alfabética coloca `read` antes de `write`, então quem rodar por glob lê o arquivo da execução anterior — foi isso que mascarou falhas no diagnóstico deste refactor.

O array `$persistenceTests` de `tests/run_tests.ps1` já está na ordem correta. Fixar a razão em comentário, para ninguém "consertar" a ordem alfabetizando:

```powershell
# ORDEM SIGNIFICATIVA: cada *_write_test.nx deixa o .db que o *_read_test.nx
# seguinte consome. Alfabetizar este array faz o read consumir o arquivo da
# execucao anterior e mascara falhas.
$persistenceTests = @(
    "persistence_write_test.nx",
    "persistence_read_test.nx",
    "deleted_write_test.nx",
    "deleted_read_test.nx",
    "history_write_test.nx",
    "history_read_test.nx",
    "empty_database_test.nx"
)
```

- [ ] **Step 6: Rodar a suíte inteira**

```powershell
.\tests\run_tests.ps1
```

Esperado: `All NoxyDB tests passed (22 files).` seguido de `OK` nos testes Python.

A conta partindo dos 19 originais: `http_transport_test.nx` saiu (−1) e `api_test.nx` entrou no lugar (+1); somaram-se `invalid_hex_utf8_test.nx` (Task 1), `read_before_write_regression_test.nx` (Task 2) e `worker_supervision_test.nx` (Task 4). Total 22.

- [ ] **Step 7: Commit**

```bash
git add examples/ tests/run_tests.ps1
git commit -m "refactor(examples): migra os call sites para ref

Documenta tambem por que a ordem do array de testes de persistencia e
significativa: cada write deixa o .db que o read seguinte consome.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10: Documentação, CHANGELOG e versões

**Files:**
- Modify: `noxy.mod:3`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `docs/noxydb-como-funciona.md` (seção 2, seções 23-24)

- [ ] **Step 1: Atualizar `noxy.mod`**

O arquivo declara `noxy v0.1.0`, que está errado desde antes deste refactor:

```
module noxydb

noxy v0.4.0
```

- [ ] **Step 2: Escrever a entrada do CHANGELOG**

Inserir no topo de `CHANGELOG.md`, acima da entrada `[0.2.0]`:

```markdown
## [0.3.0] - 2026-08-16

### Changed (BREAKING)

- **Toda função da API recebe `ref Database`.** O Noxy v0.4.0 vincula
  compostos por valor com copy-on-write, então mutar o banco através de um
  parâmetro comum deixou de ser visível para o chamador. As funções
  só-leitura também recebem `ref`: passar o struct por valor marca o estado
  como compartilhado e faz a escrita seguinte clonar a keyspace inteira
  (medido: 5.198 ms contra 15 ms em 5.000 operações). Migração:
  `noxydb.put(db, k, v)` vira `noxydb.put(ref db, k, v)`. `#database`
- **Erros de transporte HTTP usam os códigos reais** — 408, 413, 414, 431,
  501 e 505 — com corpo `text/plain`, em vez de 400 com corpo JSON. Erros de
  aplicação continuam JSON. `#server`
- **Bytes além do `Content-Length` declarado são descartados**, não
  rejeitados. O servidor fecha a conexão após uma resposta. Antes, o
  transporte artesanal respondia 400. `#server`
- **Um payload não-UTF-8 no log é `invalid database log record`**, não
  `invalid document payload`: ele agora para na camada de storage, porque
  bytes que não são UTF-8 não podem ser uma string Noxy. `#storage`

### Added

- Encerramento limpo por `SIGINT`/`SIGTERM` e pela rota `POST /v1/shutdown`,
  esta última existente apenas com `--enable-shutdown`. O worker confirma o
  fechamento físico de todos os `.db` antes de o processo sair. `#server`
- Worker supervisionado por `spawn_task`: uma falha do worker vira
  diagnóstico e saída com código 1, em vez de deixar todo request pendurado.
  `#server`
- `NoxyDBClient.shutdown()` no cliente Python. `#client`

### Fixed

- **Replay de log com hex não-UTF-8 derrubava o processo.** `to_str` levanta
  sobre bytes inválidos desde o Noxy v0.3.0, e `decode_hex` não checava.
  `#storage`
- **Corpo de request não-UTF-8 derrubava o handler**, pela mesma causa.
  `#server`
- **A linha de atividade perdia o último caractere de método e caminho.**
  `escaped_log_field` usava `substring` com o terceiro argumento como
  comprimento; ele passou a ser índice final exclusivo no Noxy v0.3.0.
  `#server`

### Removed

- `server/http_transport.nx`, 328 linhas de transporte HTTP artesanal,
  substituído pelo módulo `http_server` da stdlib. O semáforo de rede, o
  `socket_recv` byte a byte e a matemática de deadline existiam para
  contornar limitações que a linguagem resolveu. `#server`
```

- [ ] **Step 3: Atualizar o `Usage` do README**

Substituir o bloco de exemplo Noxy por:

````markdown
```noxy
use noxydb

let db: noxydb.Database = noxydb.open_database("database.db")
let profile: map[string, any] = {"city": "Cuiabá"}
let user: map[string, any] = {
    "name": "Estevao",
    "age": 30,
    "active": true,
    "languages": ["Python", "Noxy"],
    "profile": profile
}

let stored: noxydb.PutResult = noxydb.put(ref db, "user:1", user)
if stored.success then
    let result: noxydb.LookupResult = noxydb.get(ref db, "user:1")
    if result.found then
        print(result.value["name"])
    end
else
    print(stored.error)
end
noxydb.close_database(ref db)
```

Toda função da API recebe `ref Database`, inclusive as só-leitura. Chamada
qualificada de módulo é fronteira dinâmica, então o `ref` é obrigatório e
explícito — sem ele o runtime rejeita com `expected ref Database, got object`.
````

- [ ] **Step 4: Reescrever os parágrafos de limitação do README**

Remover integralmente os parágrafos que descrevem o transporte artesanal: o que começa em "Each connection has a 1,000 ms read-idle deadline" e vai até "such later bytes are discarded when this one-request-per-connection server closes the socket". Substituir por:

```markdown
O servidor usa o módulo `http_server` da stdlib do Noxy, que faz framing
incremental do bloco de headers e do corpo `Content-Length`, com orçamento
absoluto por fase como defesa contra slowloris. O limite de corpo é 1 MiB.
Requisições inválidas recebem 400, 408, 413, 414, 431, 501 ou 505 com corpo
`text/plain`; erros da API respondem em JSON. Bytes que chegam depois do corpo
declarado são descartados, porque a conexão fecha após uma resposta.

O servidor aceita conexões apenas em `127.0.0.1` e não tem autenticação,
porque é local. Não compartilhe o `.db` de um banco com outro processo NoxyDB
ao mesmo tempo.
```

- [ ] **Step 5: Substituir a seção de durabilidade do README**

Trocar o parágrafo que começa em "The current Noxy runtime exposes no signal handling to this server" por:

```markdown
`SIGINT` (Ctrl-C) e `SIGTERM` encerram o servidor de forma limpa: o listener
fecha, o worker fecha fisicamente todo `.db` em cache e só então o processo
sai. A rota `POST /v1/shutdown` faz o mesmo, e existe apenas quando o servidor
é iniciado com `--enable-shutdown`; sem a flag ela responde 404.

Terminação abrupta — `kill -9`, Task Manager, queda de energia — continua sem
rodar esse caminho, e as limitações de durabilidade a crash permanecem: não há
`fsync`. O log append-only pode ser reproduzido no restart.
```

- [ ] **Step 6: Documentar a flag no README**

Na seção `NoxyDB Server`, acrescentar após o bloco de comandos:

````markdown
```powershell
# Habilita a rota de encerramento remoto
& "D:\path\to\noxy.exe" server\noxydb_server.nx --data-dir .\data --port 8765 --enable-shutdown
```
````

- [ ] **Step 7: Reescrever a seção 2 de `docs/noxydb-como-funciona.md`**

A seção "Por que o estado em memória guarda `string` e não objeto?" justifica a serialização pelo isolamento. Isso ficou falso: o copy-on-write do v0.4.0 garante isolamento sozinho. Substituir o corpo da seção pela justificativa medida:

```markdown
O estado em memória guarda JSON serializado, não o objeto.

Antes do Noxy v0.4.0 o motivo era isolamento: compostos eram vinculados por
cópia rasa, então guardar o `map` do chamador significava compartilhar a
estrutura aninhada com ele. Serializar era a única forma de cortar esse laço.

**Esse motivo não existe mais.** O v0.4.0 vincula compostos por valor com
copy-on-write, e o isolamento passou a ser garantia da linguagem em qualquer
profundidade.

A serialização continua, por outro motivo: densidade de memória. Medido com
50 mil documentos de 147 bytes, descontado o baseline do interpretador:

| representação | 20 mil leituras | memória |
|---|---:|---:|
| `map[string, string]` serializado | 199 ms | 17,2 MB |
| `map[string, map[string, any]]` parseado | 61 ms | 158,4 MB |

Guardar o objeto parseado deixa a leitura 3,3x mais rápida e custa 9x mais
memória. Como o NoxyDB carrega o dataset inteiro em RAM, memória é o teto de
escala do projeto: a troca derrubaria o tamanho máximo de banco em quase uma
ordem de grandeza. Por isso o estado continua serializado.
```

- [ ] **Step 8: Atualizar as seções 23 e 24 de `docs/noxydb-como-funciona.md`**

A seção 23 ("O servidor por cima do NoxyDB") e a 24 ("Fluxo de uma operação via servidor") descrevem o transporte artesanal. Ler as duas seções por inteiro e reescrever para o desenho novo: `http_server` da stdlib faz o framing e spawna uma rotina por conexão; o handler traduz `HttpRequest` em `DatabaseCommand` e o envia pelo canal; o worker único serializa o acesso aos arquivos e responde pelo canal de resposta; o handler monta o `HttpResponse` e a linha de atividade.

Remover qualquer menção a semáforo de rede, leitura byte a byte, sonda de surplus ou deadline calculado por tamanho.

- [ ] **Step 9: Varrer as docs por afirmações obsoletas**

```powershell
Select-String -Path README.md,docs\noxydb-como-funciona.md -Pattern 'semaforo|semáforo|net_select|byte a byte|surplus|no signal handling|v0\.2'
```

Cada ocorrência precisa ser corrigida ou removida. `docs/noxydb-como-funciona.md` tem 1.270 linhas e só as seções 2, 23 e 24 foram identificadas na spec — esta varredura é o que pega o resto.

- [ ] **Step 10: Rodar a suíte completa uma última vez**

```powershell
$env:NOXY_EXE = 'C:\Users\estev\go\bin\noxy.exe'
.\tests\run_tests.ps1
.\tests\run_tests.ps1 -Group integration
python -m compileall -q python\src python\tests
```

Esperado: todos passam, e `compileall` sai com código 0.

- [ ] **Step 11: Commit**

```bash
git add noxy.mod CHANGELOG.md README.md docs/noxydb-como-funciona.md
git commit -m "docs: NoxyDB 0.3.0 sobre Noxy v0.4.0

noxy.mod declarava v0.1.0. A secao 2 do guia justificava a serializacao pelo
isolamento, que o copy-on-write passou a garantir sozinho; a justificativa
vira densidade de memoria, com os numeros medidos.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Verificação final

Depois da Task 10, confirmar com evidência — não por inspeção:

```powershell
$env:NOXY_EXE = 'C:\Users\estev\go\bin\noxy.exe'
.\tests\run_tests.ps1                      # 20 arquivos .nx + testes do cliente
.\tests\run_tests.ps1 -Group integration   # testes de integracao
git diff --check                           # sem espaco em branco quebrado
git status --short                         # sem arquivo esquecido
```

E confirmar que o transporte artesanal realmente saiu:

```powershell
Test-Path server\http_transport.nx         # deve ser False
```
