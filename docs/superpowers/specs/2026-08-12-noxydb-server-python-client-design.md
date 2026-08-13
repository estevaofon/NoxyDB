# NoxyDB Server and Python Client Design

## Goal

Add a persistent local NoxyDB daemon written in Noxy and a dependency-free
Python client. The network API must preserve the current NoxyDB model: a named
database contains string keys mapped directly to JSON documents. There are no
tables, collections, schemas, queries, or indexes.

## Scope

The first release provides:

- one long-running server bound exclusively to `127.0.0.1`;
- multiple logical databases managed by that server;
- implicit database creation on the first `open_database` call;
- the existing `open_database`, `put`, `get`, `remove`, `exists`, and
  `close_database` operations over HTTP;
- a Python package whose public API mirrors those operations;
- no authentication, remote access, discovery, TLS, transactions, or
  collection abstraction.

## Repository Layout

The NoxyDB repository owns both additions:

- `server/` contains the daemon, protocol types, request parsing, routing, and
  database worker code written in Noxy.
- `python/` contains the Python package, packaging metadata, and unit tests.
- `tests/` contains Noxy-side and end-to-end integration coverage.

The Noxy runtime repository is a development dependency used to execute the
server. The design does not require runtime or standard-library changes.

## Server Startup and Storage

The server starts independently of every client:

```powershell
noxy.exe server/noxydb_server.nx --data-dir .\data --port 8765
```

It starts successfully when the data directory contains no databases and
keeps running while no clients are connected. The host is fixed at
`127.0.0.1`; the CLI does not offer an option to bind externally. The default
port is `8765`, and `--port` may select another local port. `--data-dir` is
required for an explicit and predictable storage location.

Clients submit a logical database name, never a filesystem path. A valid name
has 1 through 64 ASCII characters drawn from letters, digits, `_`, and `-`.
The server resolves `usuarios` to `<data-dir>/usuarios.db`. Slashes, dots,
whitespace, traversal sequences, and every other character are rejected.

`open_database("usuarios")` opens an existing file or creates it if absent.
The server caches open databases by logical name and may keep several open at
once. Opening an already-open name reuses its server-side database. A remote
`close_database` closes only the client handle logically: because HTTP has no
durable session identity, the server acknowledges the operation but retains
the physical database until daemon shutdown. A newly opened client handle can
continue using the same database.

On a normal server shutdown, every cached database is closed. Abrupt process
termination retains the durability limitations documented by NoxyDB: writes
are append-before-mutation, but crash durability and `fsync` are not provided.

## Concurrency Model

The HTTP listener may handle multiple connections concurrently, but handlers
never access `Database` values or log files directly. Each handler validates
and decodes its request, sends one command to a single database-worker routine,
and waits on a per-command response channel.

The worker exclusively owns the map of open databases and executes commands in
receive order. Consequently all reads, writes, opens, removals, and logical
closes are serialized. This prevents concurrent mutation of Noxy maps and
append-only logs while still allowing clients to connect concurrently.

## HTTP Transport

The protocol is JSON over HTTP/1.1. The server closes the TCP connection after
one response. Every response, including errors, has
`Content-Type: application/json`, an accurate byte-based `Content-Length`, and
`Connection: close`.

The existing standard HTTP server reads only one socket chunk. NoxyDB therefore
uses `net` directly for its listener and implements bounded request assembly:

1. Read until `\r\n\r\n` is present.
2. Parse the request line and headers.
3. Accept a decimal, non-negative `Content-Length` and reject conflicting or
   malformed lengths.
4. Continue reading until exactly that many body bytes are available.
5. Reject requests whose headers plus body exceed 1 MiB.
6. Reject truncated, malformed, or surplus request bodies.

Only the methods and paths listed below are accepted. Query strings are not
part of the API.

## Version 1 API

| Method and path | JSON request | Successful JSON response |
|---|---|---|
| `GET /v1/health` | none | `{"success":true,"status":"ok"}` |
| `POST /v1/open` | `{"database":"usuarios"}` | `{"success":true,"error":""}` |
| `POST /v1/put` | `{"database":"usuarios","key":"user:1","value":{...}}` | `{"success":true,"error":""}` |
| `POST /v1/get` | `{"database":"usuarios","key":"user:1"}` | `{"found":true,"value":{...},"error":""}` |
| `POST /v1/exists` | `{"database":"usuarios","key":"user:1"}` | `{"exists":true,"error":""}` |
| `POST /v1/remove` | `{"database":"usuarios","key":"user:1"}` | `{"success":true,"error":""}` |
| `POST /v1/close` | `{"database":"usuarios"}` | `{"success":true,"error":""}` |

All POST bodies must be JSON objects containing exactly the fields required by
their operation. `database` and `key` must be strings. Keys remain unrestricted
Unicode strings, including the empty string. `value` must be a JSON object.
Unknown request fields are rejected so malformed clients fail visibly.

The stored document domain is unchanged: a JSON object at the root containing
null, booleans, signed 64-bit integers, finite 64-bit floats, strings, arrays,
and recursively string-keyed objects. `put` replaces the complete document.
An absent `get` returns HTTP 200 with `found: false`, an empty object for
`value`, and an empty error. Removing an absent key is idempotent and succeeds.

Except for `/v1/health`, database operations require a successful prior
`open_database` call by some client. The physical database may remain cached
after logical close, but a Python `Database` handle enforces its own open or
closed lifecycle locally.

## Errors and Status Codes

Every error response is a JSON object with `success: false` and a stable,
non-empty `error` string. Operation-specific fields may also be returned with
their false or empty values so response decoding remains deterministic.

- `400 Bad Request`: malformed HTTP, malformed JSON, missing or extra fields,
  invalid field types, invalid database name, invalid document, or request
  larger than 1 MiB.
- `404 Not Found`: unknown route.
- `405 Method Not Allowed`: known route with the wrong HTTP method.
- `409 Conflict`: an operation targets a database that has not been opened.
- `500 Internal Server Error`: database open, replay, append, read, or close
  failure.

Database error text is preserved when it is already part of the public NoxyDB
contract. Transport validation uses concise stable messages defined by the
server. Internal filesystem paths are not included in responses.

## Python Client

The package is installable for development with:

```powershell
python -m pip install -e .\python
```

It uses only Python's standard library, including `urllib.request`, and exposes:

```python
from noxydb import NoxyDBClient

client = NoxyDBClient("http://127.0.0.1:8765", timeout=5.0)
db = client.open_database("usuarios")

result = db.put("user:1", {"name": "Estevão", "active": True})
loaded = db.get("user:1")
present = db.exists("user:1")
db.remove("user:1")
db.close()
```

Public results mirror NoxyDB:

- `PutResult(success: bool, error: str)`;
- `LookupResult(found: bool, value: dict[str, object])`.

`NoxyDBClient.open_database(name)` calls `/v1/open` and returns a `Database`
handle. `Database.put`, `get`, `exists`, `remove`, and `close_database` mirror
the Noxy functions; `close` aliases `close_database`. A closed handle rejects
further operations locally. A later `open_database` returns a fresh handle.

Invalid Python documents fail locally when practical, including a non-dict
root, non-string object keys, circular references, non-finite floats, and
integers outside the signed 64-bit range. The server remains authoritative and
performs the same validation independently.

`NoxyDBConnectionError` represents connection, timeout, and malformed HTTP
response failures. `NoxyDBServerError` represents a valid error response from
the server and exposes its HTTP status and message. The result structs remain
useful for domain-level responses, but transport and server failures are never
silently converted into `found: false` or `success: false`.

## Verification

Tests exercise real behavior rather than implementation mocks wherever
possible:

- Noxy tests cover name validation, request parsing and framing, routing,
  command serialization, error mapping, and the 1 MiB boundary.
- Python `unittest` coverage validates serialization, result types, handle
  lifecycle, exception mapping, and public API behavior.
- End-to-end Python tests start the server using the provided `noxy.exe`, wait
  for `/v1/health`, and verify CRUD, complete replacement, implicit creation,
  persistence across restart, Unicode keys and nested documents, invalid and
  truncated logs, multiple isolated databases, fragmented HTTP requests, and
  concurrent clients.
- The existing NoxyDB test suite continues to pass unchanged, demonstrating
  that the embedded Noxy API and physical v0.2 log contract remain compatible.

## Non-Goals

This release does not add collections, tables, enumeration, queries, indexes,
partial updates, authentication, remote binding, TLS, replication, transactions,
compaction, client-driven file paths, automatic daemon startup, or changes to
the Noxy VM.
