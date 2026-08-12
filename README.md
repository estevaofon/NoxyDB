# NoxyDB

NoxyDB is a lightweight, persistent key-value database written entirely in
Noxy. Each `string` key identifies a JSON document that may contain strings,
numbers, booleans, null values, arrays, and nested objects. Documents are stored
in an append-only log and retrieved, replaced, or removed by key.

Beyond being a database project, NoxyDB serves as a real-world systems
programming workload designed to exercise and guide the evolution of the Noxy
language, its virtual machine, and its standard library.

## Architecture

```mermaid
flowchart LR
    App["Noxy application"] --> API["Database API<br/>open_database · put · get<br/>remove · exists · close_database"]

    API -- "PUT: map[string, any]" --> Serialize["document.nx<br/>serialize"]
    Serialize -- "strict JSON" --> Encode["storage.nx<br/>opaque payload · hexadecimal record"]
    API -- "REMOVE" --> Encode
    Encode --> Write["io.write_result<br/>append before mutation"]
    Write -- "success" --> State["Raw DatabaseState<br/>map[string, string]<br/>open · error · file_fd"]
    Write -- "failure" --> Failed["Database closed<br/>failed to write database log"]

    API -- "GET: reads payload" --> State
    State -- "serialized JSON" --> Deserialize["document.nx<br/>deserialize"]
    Deserialize -- "fresh map[string, any]" --> API
    API -- "EXISTS" --> State

    Write --> Log[("Append-only log<br/>P key value · D key")]

    Log -- "open_database" --> Read["Full read<br/>byte-count validation"]
    Read --> Replay["Strict replay<br/>validates and applies in order"]
    Replay --> Validate["document.deserialize<br/>final validation of every payload"]
    Validate -- "all valid" --> AppendOpen["Opens the log for append"]
    AppendOpen --> State
    Validate -- "invalid" --> Invalid["Database closed<br/>invalid document payload<br/>empty raw state"]

    API -- "close_database" --> Close["io.close_result"]
    Close -- "success" --> Closed["Database closed normally"]
    Close -- "failure" --> CloseFailed["Database closed<br/>failed to close database log"]
```

## Usage

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

## Executable example

The complete walkthrough in `examples/documents.nx` demonstrates nested
documents, reads, full replacement, removal, and replay:

```powershell
$env:NOXY_EXE = "D:\path\to\noxy.exe"
& $env:NOXY_EXE examples/documents.nx
```

The example recreates `examples/noxydb_v02.db` on every run and keeps the file
available for inspection afterward. Databases generated inside `examples/`
are ignored by Git.

### Interactive user registry

`examples/cadastro_usuarios.nx` ports the original SQLite user registry to
NoxyDB. Each user has a dedicated key, while `usuarios:meta` stores the next ID
and the index used for listing:

```powershell
& $env:NOXY_EXE examples/cadastro_usuarios.nx
```

The database is stored at `examples/usuarios.db` and is ignored by Git.

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
