# NoxyDB

NoxyDB é um banco de dados chave-valor persistente e leve, escrito inteiramente
em Noxy. Cada chave `string` identifica um documento JSON, que pode conter
strings, números, booleanos, valores nulos, arrays e objetos aninhados. Os
documentos são armazenados em um log somente de acréscimo e recuperados,
substituídos ou removidos por sua chave.

Mais do que um projeto de banco de dados, o NoxyDB funciona como uma carga de
trabalho real de programação de sistemas, criada para exercitar e orientar a
evolução da linguagem Noxy, de sua máquina virtual e de sua biblioteca padrão.

## Arquitetura

```mermaid
flowchart LR
    App["Aplicação Noxy"] --> API["Database API<br/>open_database · put · get<br/>remove · exists · close_database"]

    API -- "PUT: map[string, any]" --> Serialize["document.nx<br/>serialize"]
    Serialize -- "JSON estrito" --> Encode["storage.nx<br/>payload opaco · registro hexadecimal"]
    API -- "REMOVE" --> Encode
    Encode --> Write["io.write_result<br/>append antes da mutação"]
    Write -- "sucesso" --> State["DatabaseState bruto<br/>map[string, string]<br/>open · error · file_fd"]
    Write -- "falha" --> Failed["Banco fechado<br/>failed to write database log"]

    API -- "GET: lê payload" --> State
    State -- "JSON serializado" --> Deserialize["document.nx<br/>deserialize"]
    Deserialize -- "novo map[string, any]" --> API
    API -- "EXISTS" --> State

    Write --> Log[("Append-only log<br/>P key value · D key")]

    Log -- "open_database" --> Read["Leitura integral<br/>validação de tamanho"]
    Read --> Replay["Replay estrito<br/>valida e aplica em ordem"]
    Replay --> Validate["document.deserialize<br/>validação final de todos os payloads"]
    Validate -- "todos válidos" --> AppendOpen["Abre o log para append"]
    AppendOpen --> State
    Validate -- "inválido" --> Invalid["Banco fechado<br/>invalid document payload<br/>estado bruto vazio"]

    API -- "close_database" --> Close["io.close_result"]
    Close -- "sucesso" --> Closed["Banco fechado normalmente"]
    Close -- "falha" --> CloseFailed["Banco fechado<br/>failed to close database log"]
```

## Uso

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

let stored: noxydb.PutResult = noxydb.put(db, "user:1", user)
if stored.success then
    let result: noxydb.LookupResult = noxydb.get(db, "user:1")
    if result.found then
        print(result.value["name"])
    end
else
    print(stored.error)
end
noxydb.close_database(db)
```

## Exemplo executável

O walkthrough completo em `examples/documents.nx` demonstra documentos
aninhados, leitura, substituição completa, remoção e replay:

```powershell
$env:NOXY_EXE = "D:\caminho\para\noxy.exe"
& $env:NOXY_EXE examples/documents.nx
```

O exemplo recria `examples/noxydb_v02.db` a cada execução e mantém o arquivo ao
final para inspeção. Bancos gerados dentro de `examples/` são ignorados pelo
Git.

## API

NoxyDB v0.2 maps string keys to JSON objects represented as
map[string, any]. Scalars, arrays, and null are valid inside a document but not
at its root.

LookupResult contains found: bool and value: map[string, any]. PutResult
contains success: bool and error: string. An existing empty object returns
found == true; an absent key returns found == false.

put() replaces the complete document. It returns document is not
JSON-compatible for invalid caller values without failing the database. An I/O
append failure returns failed to write database log and transitions the
database to failed.

## JSON domain

Documents may contain null, bool, signed 64-bit int, finite 64-bit float,
string, arrays, and recursively string-keyed maps. Bytes, structs, references,
callables, channels, wait groups, non-string map keys, NaN, infinities, and
cycles are rejected.

## State and isolation

The authoritative in-memory state is map[string, string] containing serialized
JSON. get() deserializes a fresh map on every successful lookup. Mutating the
input after put(), or mutating a returned document, cannot change database
state or persistence.

## Physical format and replay

P<TAB><key_hex><TAB><payload_hex>\n
D<TAB><key_hex>\n

storage.nx treats payloads as opaque strings. Replay strictly validates record
termination, arity, operations, hexadecimal data, and read byte count. The API
then validates every replayed payload as a JSON object before opening the file
for append.

There is no header, migration, fallback, version discriminator, or v0.1
compatibility logic.

## Lifecycle and durability

The observable states remain open, normally closed, and failed. Writes reach
the append-only log before the raw in-memory map changes. Write and close
failures are explicit. Persistence is guaranteed after close_database()
completes successfully; crash durability and fsync are not provided.

Queries, JSON Path, partial updates, indexes, schemas, collections, filters,
compaction, TTL, networking, concurrency, transactions, replication, and
sharding remain out of scope.
