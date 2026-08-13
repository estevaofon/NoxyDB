# NoxyDB Server and Python Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent localhost-only Noxy daemon that serves multiple named NoxyDB databases over JSON/HTTP and a dependency-free Python client that mirrors the embedded NoxyDB API.

**Architecture:** A custom Noxy TCP listener assembles one bounded HTTP/1.1 request per connection and dispatches decoded operations to one channel-backed database worker. The worker exclusively owns all open `noxydb.Database` values. The Python package uses `urllib.request`, validates documents locally, and exposes typed result objects and database handles.

**Tech Stack:** Noxy v0.1.0 modules (`net`, `io`, `strings`, `json`, `sys`, channels), NoxyDB v0.2, Python 3.10+ standard library (`urllib`, `dataclasses`, `unittest`), PowerShell test runner.

## Global Constraints

- Bind exclusively to `127.0.0.1`; do not add a configurable host.
- Default to port `8765`; accept an optional `--port` integer.
- Require `--data-dir`; create the final directory when it does not exist.
- A database name is 1–64 ASCII letters, digits, `_`, or `-` and resolves only to `<data-dir>/<name>.db`.
- Preserve the NoxyDB model of string keys mapped directly to root JSON objects; do not introduce tables or collections.
- Keep physical v0.2 log compatibility and do not modify the Noxy VM repository.
- Limit each complete HTTP request to 1 MiB and close the connection after one response.
- Serialize all database operations through one Noxy worker routine.
- Keep the Python package dependency-free and support Python 3.10 or newer.
- Preserve the unrelated untracked `cadastro_usuarios.nx` file.

## File Structure

- `server/protocol.nx`: protocol DTOs, database-name validation, strict request decoding, response envelopes, and route dispatch into the worker.
- `server/database_worker.nx`: command/result DTOs and the single-owner database loop.
- `server/http_transport.nx`: bounded HTTP request assembly, response encoding, client handling, and accept loop.
- `server/noxydb_server.nx`: CLI parsing, data-directory preparation, worker startup, and daemon entry point.
- `tests/server_protocol_test.nx`: pure protocol and validation tests.
- `tests/database_worker_test.nx`: worker CRUD, isolation, and lifecycle tests using temporary test databases.
- `tests/http_transport_test.nx`: pure HTTP framing and response-building tests.
- `python/pyproject.toml`: Python package metadata and Python version floor.
- `python/src/noxydb/models.py`: immutable `PutResult` and `LookupResult` dataclasses.
- `python/src/noxydb/errors.py`: public exception hierarchy.
- `python/src/noxydb/client.py`: JSON-domain validation, HTTP transport, `NoxyDBClient`, and `Database`.
- `python/src/noxydb/__init__.py`: stable public exports.
- `python/tests/test_client.py`: Python unit tests against an in-process HTTP fixture.
- `python/tests/test_integration.py`: real daemon tests, including persistence and concurrency.
- `.gitignore`: Python bytecode, editable-install metadata, and the explicit temporary virtual environment.
- `tests/run_tests.ps1`: include the new finite Noxy tests and run Python unit/integration suites when requested.
- `README.md`: daemon startup, Python installation, API example, protocol summary, and limitations.

---

### Task 1: Protocol Contracts and Strict Request Validation

**Files:**
- Create: `server/protocol.nx`
- Create: `tests/server_protocol_test.nx`
- Modify: `tests/run_tests.ps1`

**Interfaces:**
- Consumes: Noxy `json_loads`, `json.dumps_result`, `keys`, and string helpers.
- Produces: `ApiRequest`, `ApiResponse`, `valid_database_name(name: string) -> bool`, `decode_api_request(operation: string, body: bytes) -> ApiRequest`, `api_success(payload: map[string, any]) -> ApiResponse`, and `api_error(status: int, error: string) -> ApiResponse`.

- [ ] **Step 1: Write failing protocol tests**

Create `tests/server_protocol_test.nx` with direct assertions for the accepted name boundary, rejected traversal and punctuation, exact request fields, root-object enforcement, Unicode keys, and response JSON:

```noxy
use server.protocol as protocol
use tests.assertions as assertions
use strings

assertions.assert_true(protocol.valid_database_name("usuarios"), "letters should be valid")
assertions.assert_true(protocol.valid_database_name("db_2026-test"), "safe punctuation should be valid")
assertions.assert_true(protocol.valid_database_name("a"), "one character should be valid")
assertions.assert_true(protocol.valid_database_name("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), "64 characters should be valid")
assertions.assert_false(protocol.valid_database_name(""), "empty name should be rejected")
assertions.assert_false(protocol.valid_database_name("../usuarios"), "traversal should be rejected")
assertions.assert_false(protocol.valid_database_name("usuarios.db"), "dot should be rejected")
assertions.assert_false(protocol.valid_database_name("á"), "non-ASCII name should be rejected")

let open_request: protocol.ApiRequest = protocol.decode_api_request("open", b"{\"database\":\"usuarios\"}")
assertions.assert_true(open_request.valid, "valid open request should decode")
assertions.assert_string(open_request.database, "usuarios", "database should decode")

let put_request: protocol.ApiRequest = protocol.decode_api_request("put", b"{\"database\":\"usuarios\",\"key\":\"usuário:1\",\"value\":{\"active\":true}}")
assertions.assert_true(put_request.valid, "valid put request should decode")
assertions.assert_string(put_request.key, "usuário:1", "Unicode key should decode")
assertions.assert_true(put_request.value["active"], "document should decode")

let extra: protocol.ApiRequest = protocol.decode_api_request("open", b"{\"database\":\"usuarios\",\"extra\":true}")
assertions.assert_false(extra.valid, "extra field should be rejected")
assertions.assert_string(extra.error, "invalid request fields", "extra field error should be stable")

let scalar: protocol.ApiRequest = protocol.decode_api_request("put", b"{\"database\":\"usuarios\",\"key\":\"k\",\"value\":1}")
assertions.assert_false(scalar.valid, "scalar document should be rejected")

let malformed: protocol.ApiRequest = protocol.decode_api_request("get", b"{")
assertions.assert_false(malformed.valid, "malformed JSON should be rejected")
assertions.assert_string(malformed.error, "invalid JSON request", "JSON error should be stable")

let response: protocol.ApiResponse = protocol.api_error(409, "database is not open")
assertions.assert_int(response.status, 409, "status should be retained")
assertions.assert_true(strings.contains(response.json, "\"success\":false"), "error JSON should expose success")
assertions.assert_true(strings.contains(response.json, "\"error\":\"database is not open\""), "error JSON should expose message")

print("server protocol tests passed")
```

Add a new server group in `tests/run_tests.ps1` and include it in the default finite suite:

```powershell
$serverTests = @(
    "server_protocol_test.nx"
)

$tests = if (-not [string]::IsNullOrWhiteSpace($Test)) {
    @($Test)
} elseif ($Group -eq "persistence") {
    $persistenceTests
} elseif ($Group -eq "errors") {
    $errorTests
} elseif ($Group -eq "server") {
    $serverTests
} elseif ([string]::IsNullOrWhiteSpace($Group)) {
    $coreTests + $persistenceTests + $errorTests + $serverTests
} else {
    throw "Unknown test group: $Group"
}
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
$env:NOXY_EXE = "D:\OneDrive\Documentos\go_projects\noxy\noxy.exe"
.\tests\run_tests.ps1 -Test server_protocol_test.nx
```

Expected: nonzero exit because `server.protocol` does not exist.

- [ ] **Step 3: Implement the minimal protocol module**

Create the following contracts in `server/protocol.nx`:

```noxy
use json
use strings

struct ApiRequest
    valid: bool
    operation: string
    database: string
    key: string
    value: map[string, any]
    error: string
end

struct ApiResponse
    status: int
    json: string
end

struct DatabaseBody
    database: string
end

struct KeyBody
    database: string
    key: string
end

struct PutBody
    database: string
    key: string
    value: map[string, any]
end

func invalid_request(operation: string, error: string) -> ApiRequest
    let empty: map[string, any]
    return ApiRequest(false, operation, "", "", empty, error)
end

func valid_database_name(name: string) -> bool
    if length(name) < 1 || length(name) > 64 then return false end
    let index: int = 0
    while index < length(name) do
        let character: string = strings.char_at(name, index)
        if !strings.contains("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-", character) then
            return false
        end
        index = index + 1
    end
    return true
end

func expected_field_count(operation: string) -> int
    if operation == "put" then return 3 end
    if operation == "get" || operation == "exists" || operation == "remove" then return 2 end
    if operation == "open" || operation == "close" then return 1 end
    return -1
end

func decode_api_request(operation: string, body: bytes) -> ApiRequest
    let raw: map[string, any] = {}
    let body_string: string = to_str(body)
    if !json_loads(body_string, raw) then return invalid_request(operation, "invalid JSON request") end
    let expected: int = expected_field_count(operation)
    if expected == -1 || length(keys(raw)) != expected then return invalid_request(operation, "invalid request fields") end
    if !has_key(raw, "database") then return invalid_request(operation, "invalid request fields") end
    if operation == "put" then
        if !has_key(raw, "key") || !has_key(raw, "value") then return invalid_request(operation, "invalid request fields") end
        let decoded: PutBody = PutBody("", "", {})
        if !json_loads(body_string, ref decoded) then return invalid_request(operation, "invalid request fields") end
        if !valid_database_name(decoded.database) then return invalid_request(operation, "invalid database name") end
        let encoded: json.EncodeResult = json.dumps_result(decoded.value)
        if !encoded.success then return invalid_request(operation, "invalid document") end
        return ApiRequest(true, operation, decoded.database, decoded.key, decoded.value, "")
    end

    if operation == "get" || operation == "exists" || operation == "remove" then
        if !has_key(raw, "key") then return invalid_request(operation, "invalid request fields") end
        let decoded: KeyBody = KeyBody("", "")
        if !json_loads(body_string, ref decoded) then return invalid_request(operation, "invalid request fields") end
        if !valid_database_name(decoded.database) then return invalid_request(operation, "invalid database name") end
        let empty: map[string, any] = {}
        return ApiRequest(true, operation, decoded.database, decoded.key, empty, "")
    end

    let decoded: DatabaseBody = DatabaseBody("")
    if !json_loads(body_string, ref decoded) then return invalid_request(operation, "invalid request fields") end
    if !valid_database_name(decoded.database) then return invalid_request(operation, "invalid database name") end
    let empty: map[string, any] = {}
    return ApiRequest(true, operation, decoded.database, "", empty, "")
end

func encode_response(payload: map[string, any]) -> string
    let encoded: json.EncodeResult = json.dumps_result(payload)
    if encoded.success then return encoded.data end
    return "{\"success\":false,\"error\":\"failed to encode response\"}"
end

func api_success(payload: map[string, any]) -> ApiResponse
    return ApiResponse(200, encode_response(payload))
end

func api_error(status: int, error: string) -> ApiResponse
    let payload: map[string, any] = {"success": false, "error": error}
    return ApiResponse(status, encode_response(payload))
end
```

The initial generic map decode proves a root object and exact key set without reading dynamically typed fields. The second decode into `DatabaseBody`, `KeyBody`, or `PutBody` validates field types before values enter typed locals.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the command from Step 2. Expected: `server protocol tests passed` followed by `All NoxyDB tests passed (1 files).`

- [ ] **Step 5: Run the existing NoxyDB suite**

Run:

```powershell
.\tests\run_tests.ps1
```

Expected: all existing tests plus the new finite protocol test pass.

- [ ] **Step 6: Commit the protocol slice**

```powershell
git add server/protocol.nx tests/server_protocol_test.nx tests/run_tests.ps1
git commit -m "feat: define NoxyDB server protocol"
```

---

### Task 2: Single-Owner Database Worker

**Files:**
- Create: `server/database_worker.nx`
- Create: `tests/database_worker_test.nx`
- Modify: `tests/run_tests.ps1`

**Interfaces:**
- Consumes: `noxydb.Database`, `noxydb.PutResult`, `noxydb.LookupResult`, and `server.protocol.ApiResponse`.
- Produces: `DatabaseCommand`, `new_database_command(operation: string, database: string, key: string, value: map[string, any]) -> DatabaseCommand`, `run_database_worker(commands: chan any, data_dir: string) -> void`, and `execute_database_command(databases: map[string, noxydb.Database], data_dir: string, command: DatabaseCommand) -> ApiResponse`.

- [ ] **Step 1: Write failing worker tests**

Create `tests/database_worker_test.nx`. Use `tests/tmp_server_worker` and remove only its explicit database files before and after the test. Exercise the synchronous `execute_database_command` seam so the finite test cannot hang:

```noxy
use io
use noxydb
use server.database_worker as worker
use server.protocol as protocol
use tests.assertions as assertions
use strings

let data_dir: string = "tests/tmp_server_worker"
if !io.exists(data_dir) then assertions.assert_true(io.mkdir(data_dir), "test directory should be created") end
let first_path: string = data_dir + "/usuarios.db"
let second_path: string = data_dir + "/pedidos.db"
if io.exists(first_path) then io.remove(first_path) end
if io.exists(second_path) then io.remove(second_path) end

let databases: map[string, noxydb.Database]

let open_users: protocol.ApiResponse = worker.execute_database_command(databases, data_dir, worker.new_database_command("open", "usuarios", "", {}))
assertions.assert_int(open_users.status, 200, "open should succeed")
assertions.assert_true(io.exists(first_path), "open should create database")

let document: map[string, any] = {"name": "Estevão", "active": true}
let put_user: protocol.ApiResponse = worker.execute_database_command(databases, data_dir, worker.new_database_command("put", "usuarios", "user:1", document))
assertions.assert_int(put_user.status, 200, "put should succeed")

let get_user: protocol.ApiResponse = worker.execute_database_command(databases, data_dir, worker.new_database_command("get", "usuarios", "user:1", {}))
assertions.assert_true(strings.contains(get_user.json, "Estevão"), "get should return stored document")

let missing: protocol.ApiResponse = worker.execute_database_command(databases, data_dir, worker.new_database_command("get", "usuarios", "missing", {}))
assertions.assert_true(strings.contains(missing.json, "\"found\":false"), "missing get should be explicit")

let before_open: protocol.ApiResponse = worker.execute_database_command(databases, data_dir, worker.new_database_command("exists", "pedidos", "order:1", {}))
assertions.assert_int(before_open.status, 409, "unopened database should conflict")

let open_orders: protocol.ApiResponse = worker.execute_database_command(databases, data_dir, worker.new_database_command("open", "pedidos", "", {}))
assertions.assert_int(open_orders.status, 200, "second database should open")
let isolated: protocol.ApiResponse = worker.execute_database_command(databases, data_dir, worker.new_database_command("exists", "pedidos", "user:1", {}))
assertions.assert_true(strings.contains(isolated.json, "\"exists\":false"), "databases should be isolated")

let remove_user: protocol.ApiResponse = worker.execute_database_command(databases, data_dir, worker.new_database_command("remove", "usuarios", "user:1", {}))
assertions.assert_int(remove_user.status, 200, "remove should succeed")
let remove_again: protocol.ApiResponse = worker.execute_database_command(databases, data_dir, worker.new_database_command("remove", "usuarios", "user:1", {}))
assertions.assert_int(remove_again.status, 200, "remove should be idempotent")

worker.close_all_databases(databases)
if io.exists(first_path) then io.remove(first_path) end
if io.exists(second_path) then io.remove(second_path) end
print("database worker tests passed")
```

Add this test to `$serverTests`.

- [ ] **Step 2: Run the worker test and verify RED**

Run:

```powershell
.\tests\run_tests.ps1 -Test database_worker_test.nx
```

Expected: nonzero exit because `server.database_worker` does not exist.

- [ ] **Step 3: Implement command execution and worker ownership**

Create `server/database_worker.nx` with these concrete contracts:

```noxy
use noxydb
use server.protocol as protocol

struct DatabaseCommand
    operation: string
    database: string
    key: string
    value: map[string, any]
    response: any
end

func new_database_command(operation: string, database: string, key: string, value: map[string, any]) -> DatabaseCommand
    return DatabaseCommand(operation, database, key, value, null)
end

func database_path(data_dir: string, name: string) -> string
    return data_dir + "/" + name + ".db"
end

func execute_database_command(databases: map[string, noxydb.Database], data_dir: string, command: DatabaseCommand) -> protocol.ApiResponse
    if command.operation == "open" then
        if has_key(databases, command.database) then
            let cached: noxydb.Database = databases[command.database]
            if !noxydb.is_open(cached) then return protocol.api_error(500, noxydb.database_error(cached)) end
        else
            let db: noxydb.Database = noxydb.open_database(database_path(data_dir, command.database))
            if !noxydb.is_open(db) then return protocol.api_error(500, noxydb.database_error(db)) end
            databases[command.database] = db
        end
        return protocol.api_success({"success": true, "error": ""})
    end
    if !has_key(databases, command.database) then return protocol.api_error(409, "database is not open") end

    let db: noxydb.Database = databases[command.database]
    if !noxydb.is_open(db) then return protocol.api_error(500, noxydb.database_error(db)) end
    if command.operation == "put" then
        let result: noxydb.PutResult = noxydb.put(db, command.key, command.value)
        if !result.success then return protocol.api_error(500, result.error) end
        return protocol.api_success({"success": true, "error": ""})
    end
    if command.operation == "get" then
        let result: noxydb.LookupResult = noxydb.get(db, command.key)
        if noxydb.database_error(db) != "" then return protocol.api_error(500, noxydb.database_error(db)) end
        return protocol.api_success({"found": result.found, "value": result.value, "error": ""})
    end
    if command.operation == "exists" then
        return protocol.api_success({"exists": noxydb.exists(db, command.key), "error": ""})
    end
    if command.operation == "remove" then
        noxydb.remove(db, command.key)
        if noxydb.database_error(db) != "" then return protocol.api_error(500, noxydb.database_error(db)) end
        return protocol.api_success({"success": true, "error": ""})
    end
    if command.operation == "close" then
        return protocol.api_success({"success": true, "error": ""})
    end
    return protocol.api_error(404, "unknown operation")
end

func run_database_worker(commands: chan any, data_dir: string) -> void
    let databases: map[string, noxydb.Database]
    while true do
        let raw: any = chan_recv(commands)
        if raw == null && chan_is_closed(commands) then break end
        let command: DatabaseCommand = raw
        let response: protocol.ApiResponse = execute_database_command(databases, data_dir, command)
        chan_send(command.response, response)
    end
    close_all_databases(databases)
end

func close_all_databases(databases: map[string, noxydb.Database]) -> void
    let names: string[] = keys(databases)
    let index: int = 0
    while index < length(names) do
        noxydb.close_database(databases[names[index]])
        index = index + 1
    end
end
```

When routing a live request, construct `DatabaseCommand` with a new unbuffered response channel, set `command.response`, send it to `commands`, then receive exactly one `ApiResponse`. Keep all access to the database map inside `run_database_worker` or the synchronous test seam.

- [ ] **Step 4: Run focused and full Noxy tests**

Run:

```powershell
.\tests\run_tests.ps1 -Test database_worker_test.nx
.\tests\run_tests.ps1
```

Expected: both commands exit zero; the focused run prints `database worker tests passed`.

- [ ] **Step 5: Commit the worker slice**

```powershell
git add server/database_worker.nx tests/database_worker_test.nx tests/run_tests.ps1
git commit -m "feat: serialize server database operations"
```

---

### Task 3: Bounded HTTP Transport and Daemon CLI

**Files:**
- Create: `server/http_transport.nx`
- Create: `server/noxydb_server.nx`
- Create: `tests/http_transport_test.nx`
- Modify: `tests/run_tests.ps1`

**Interfaces:**
- Consumes: `net.Socket`, `server.protocol.ApiRequest`, `server.protocol.ApiResponse`, and the database command channel.
- Produces: `HttpReadResult`, `assemble_request(buffer: bytes) -> HttpReadResult`, `build_http_response(response: ApiResponse) -> bytes`, `route_request(request: HttpReadResult, commands: chan any) -> ApiResponse`, and `serve_local(port: int, commands: chan any) -> void`.

- [ ] **Step 1: Write failing pure transport tests**

Create `tests/http_transport_test.nx`:

```noxy
use server.http_transport as transport
use server.protocol as protocol
use tests.assertions as assertions
use strings

let complete: bytes = b"POST /v1/open HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 23\r\n\r\n{\"database\":\"usuarios\"}"
let parsed: transport.HttpReadResult = transport.assemble_request(complete)
assertions.assert_true(parsed.complete, "complete request should parse")
assertions.assert_true(parsed.valid, "complete request should be valid")
assertions.assert_string(parsed.method, "POST", "method should parse")
assertions.assert_string(parsed.path, "/v1/open", "path should parse")
assertions.assert_string(to_str(parsed.body), "{\"database\":\"usuarios\"}", "body should parse")

let partial: transport.HttpReadResult = transport.assemble_request(b"POST /v1/open HTTP/1.1\r\nContent-Length: 23\r\n\r\n{\"data")
assertions.assert_false(partial.complete, "partial body should request more bytes")

let malformed_length: transport.HttpReadResult = transport.assemble_request(b"POST /v1/open HTTP/1.1\r\nContent-Length: abc\r\n\r\n")
assertions.assert_false(malformed_length.valid, "non-decimal length should be rejected")
assertions.assert_string(malformed_length.error, "invalid Content-Length", "length error should be stable")

let duplicate_length: transport.HttpReadResult = transport.assemble_request(b"POST /v1/open HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n")
assertions.assert_false(duplicate_length.valid, "duplicate length should be rejected")

let response: bytes = transport.build_http_response(protocol.ApiResponse(200, "{\"success\":true}"))
let response_text: string = to_str(response)
assertions.assert_true(strings.starts_with(response_text, "HTTP/1.1 200 OK\r\n"), "status line should be correct")
assertions.assert_true(strings.contains(response_text, "Content-Type: application/json\r\n"), "content type should be JSON")
assertions.assert_true(strings.contains(response_text, "Content-Length: 16\r\n"), "content length should count bytes")
assertions.assert_true(strings.ends_with(response_text, "\r\n\r\n{\"success\":true}"), "body should follow headers")

print("HTTP transport tests passed")
```

Add it to `$serverTests`.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\tests\run_tests.ps1 -Test http_transport_test.nx
```

Expected: nonzero exit because `server.http_transport` does not exist.

- [ ] **Step 3: Implement pure framing and routing**

Define this transport shape in `server/http_transport.nx`:

```noxy
use net
use strings
use server.protocol as protocol
use server.database_worker as worker

let MAX_REQUEST_BYTES: int = 1048576

struct HttpReadResult
    complete: bool
    valid: bool
    method: string
    path: string
    body: bytes
    expected_bytes: int
    error: string
end
```

Implement `assemble_request` by scanning the byte buffer for the four bytes `13, 10, 13, 10`, converting only the header slice to text, splitting the request line into exactly three tokens, scanning headers case-insensitively, accepting exactly zero or one `Content-Length`, validating every length character with `strings.is_digit`, and comparing body bytes rather than characters. Return `complete: false` only when more bytes can make the request valid. Return `valid: false` for malformed/surplus bodies or anything above `MAX_REQUEST_BYTES`.

Implement route mapping exactly as follows:

```noxy
func operation_for(method: string, path: string) -> string
    if method == "POST" && path == "/v1/open" then return "open" end
    if method == "POST" && path == "/v1/put" then return "put" end
    if method == "POST" && path == "/v1/get" then return "get" end
    if method == "POST" && path == "/v1/exists" then return "exists" end
    if method == "POST" && path == "/v1/remove" then return "remove" end
    if method == "POST" && path == "/v1/close" then return "close" end
    return ""
end

func route_request(request: HttpReadResult, commands: chan any) -> protocol.ApiResponse
    if request.method == "GET" && request.path == "/v1/health" then
        return protocol.api_success({"success": true, "status": "ok"})
    end
    let operation: string = operation_for(request.method, request.path)
    if operation == "" then
        if request.path == "/v1/health" || request.path == "/v1/open" || request.path == "/v1/put" || request.path == "/v1/get" || request.path == "/v1/exists" || request.path == "/v1/remove" || request.path == "/v1/close" then
            return protocol.api_error(405, "method not allowed")
        end
        return protocol.api_error(404, "not found")
    end
    let decoded: protocol.ApiRequest = protocol.decode_api_request(operation, request.body)
    if !decoded.valid then return protocol.api_error(400, decoded.error) end
    let reply: any = make_chan(0)
    let command: worker.DatabaseCommand = worker.DatabaseCommand(decoded.operation, decoded.database, decoded.key, decoded.value, reply)
    chan_send(commands, command)
    let response: protocol.ApiResponse = chan_recv(reply)
    chan_close(reply)
    return response
end
```

`build_http_response` must map 200/400/404/405/409/500 to standard reason phrases and calculate `Content-Length` with `length(to_bytes(response.json))`.

- [ ] **Step 4: Implement socket reading, connection handling, and CLI**

In `http_transport.nx`, make `read_http_request(client)` loop on `net.socket_recv(client, 65536)`, append received bytes, call `assemble_request` after each chunk, and stop on complete, invalid, EOF, or the 1 MiB boundary. Make `handle_client` always send one JSON response, loop until `net.socket_send` has written every response byte or reports failure, and close the socket. Make `serve_local` bind only `127.0.0.1`, accept forever, and `spawn(handle_client, client, commands)` for each open client.

Create `server/noxydb_server.nx` with strict flag parsing:

```noxy
use io
use sys
use server.database_worker as worker
use server.http_transport as transport

struct ServerOptions
    valid: bool
    data_dir: string
    port: int
    error: string
end

func parse_options(args: string[]) -> ServerOptions
    let data_dir: string = ""
    let port: int = 8765
    let index: int = 2
    while index < length(args) do
        if args[index] == "--data-dir" && index + 1 < length(args) then
            data_dir = args[index + 1]
            index = index + 2
        elif args[index] == "--port" && index + 1 < length(args) then
            port = to_int(args[index + 1])
            index = index + 2
        else
            return ServerOptions(false, "", 0, "usage: noxy noxydb_server.nx --data-dir <path> [--port <1-65535>]")
        end
    end
    if data_dir == "" then return ServerOptions(false, "", 0, "--data-dir is required") end
    if port < 1 || port > 65535 then return ServerOptions(false, "", 0, "port must be between 1 and 65535") end
    return ServerOptions(true, data_dir, port, "")
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
spawn(worker.run_database_worker, commands, options.data_dir)
print("NoxyDB server listening on http://127.0.0.1:" + to_str(options.port))
transport.serve_local(options.port, commands)
chan_close(commands)
```

The option parser must validate the `--port` token as decimal before calling `to_int`, so malformed text cannot silently select a port.

- [ ] **Step 5: Run transport, CLI smoke, and full Noxy tests**

Run:

```powershell
.\tests\run_tests.ps1 -Test http_transport_test.nx
& $env:NOXY_EXE server\noxydb_server.nx
```

Expected: the test passes; the second command exits with code 2 and prints `--data-dir is required`.

Then run `.\tests\run_tests.ps1`. Expected: all finite Noxy tests pass.

- [ ] **Step 6: Commit the daemon slice**

```powershell
git add server/http_transport.nx server/noxydb_server.nx tests/http_transport_test.nx tests/run_tests.ps1
git commit -m "feat: serve NoxyDB over local HTTP"
```

---

### Task 4: Dependency-Free Python Client

**Files:**
- Modify: `.gitignore`
- Create: `python/pyproject.toml`
- Create: `python/src/noxydb/models.py`
- Create: `python/src/noxydb/errors.py`
- Create: `python/src/noxydb/client.py`
- Create: `python/src/noxydb/__init__.py`
- Create: `python/tests/test_client.py`

**Interfaces:**
- Consumes: Version 1 HTTP routes and response envelopes from Task 3.
- Produces: `NoxyDBClient`, `Database`, `PutResult`, `LookupResult`, `NoxyDBError`, `NoxyDBConnectionError`, `NoxyDBServerError`, and `NoxyDBValidationError`.

- [ ] **Step 1: Write failing public API and validation tests**

Create `python/tests/test_client.py` using an in-process `ThreadingHTTPServer` fixture that records requests and returns queued JSON responses. Cover exact paths and bodies, Unicode, typed results, missing lookup, close lifecycle, server errors, malformed responses, connection failure, cycles, non-finite floats, non-string keys, non-dict roots, and signed 64-bit integer boundaries. Representative tests:

```python
import json
import math
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from noxydb import (
    LookupResult,
    NoxyDBClient,
    NoxyDBConnectionError,
    NoxyDBServerError,
    NoxyDBValidationError,
    PutResult,
)


class ClientTests(unittest.TestCase):
    def test_crud_mirrors_noxydb_api(self):
        self.server.responses.extend([
            (200, {"success": True, "error": ""}),
            (200, {"success": True, "error": ""}),
            (200, {"found": True, "value": {"name": "Estevão"}, "error": ""}),
            (200, {"exists": True, "error": ""}),
            (200, {"success": True, "error": ""}),
            (200, {"success": True, "error": ""}),
        ])
        db = self.client.open_database("usuarios")
        self.assertEqual(db.put("user:1", {"name": "Estevão"}), PutResult(True, ""))
        self.assertEqual(db.get("user:1"), LookupResult(True, {"name": "Estevão"}))
        self.assertTrue(db.exists("user:1"))
        self.assertIsNone(db.remove("user:1"))
        self.assertIsNone(db.close())
        self.assertEqual([request[0] for request in self.server.requests], [
            "/v1/open", "/v1/put", "/v1/get", "/v1/exists", "/v1/remove", "/v1/close"
        ])

    def test_closed_handle_rejects_operations_locally(self):
        self.server.responses.extend([
            (200, {"success": True, "error": ""}),
            (200, {"success": True, "error": ""}),
        ])
        db = self.client.open_database("usuarios")
        db.close_database()
        with self.assertRaisesRegex(NoxyDBValidationError, "database handle is closed"):
            db.get("user:1")

    def test_rejects_values_outside_json_domain(self):
        self.server.responses.append((200, {"success": True, "error": ""}))
        db = self.client.open_database("usuarios")
        invalid = [math.nan, math.inf, 2**63, -(2**63) - 1]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(NoxyDBValidationError):
                    db.put("key", {"value": value})
        cyclic = {}
        cyclic["self"] = cyclic
        with self.assertRaises(NoxyDBValidationError):
            db.put("key", cyclic)

    def test_server_error_exposes_status_and_message(self):
        self.server.responses.append((409, {"success": False, "error": "database is not open"}))
        with self.assertRaises(NoxyDBServerError) as raised:
            self.client._request("/v1/get", {"database": "usuarios", "key": "k"})
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(str(raised.exception), "database is not open")
```

The fixture must send UTF-8 JSON with accurate `Content-Length` and suppress access logging.

- [ ] **Step 2: Verify RED**

Run:

```powershell
$env:PYTHONPATH = "$PWD\python\src"
python -m unittest discover -s python\tests -p "test_client.py" -v
```

Expected: import failure because package files do not exist.

- [ ] **Step 3: Add package metadata, result types, and errors**

Append these Python-only generated artifacts to `.gitignore`:

```gitignore
__pycache__/
*.py[cod]
*.egg-info/
.tmp-noxydb-venv/
```

Create `python/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "noxydb"
version = "0.1.0"
description = "Python client for the local NoxyDB server"
requires-python = ">=3.10"
dependencies = []

[tool.setuptools.packages.find]
where = ["src"]
```

Create immutable dataclasses in `models.py`:

```python
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True, slots=True)
class PutResult:
    success: bool
    error: str

@dataclass(frozen=True, slots=True)
class LookupResult:
    found: bool
    value: dict[str, Any]
```

Create the exception hierarchy in `errors.py`:

```python
class NoxyDBError(Exception):
    pass

class NoxyDBValidationError(NoxyDBError, ValueError):
    pass

class NoxyDBConnectionError(NoxyDBError, ConnectionError):
    pass

class NoxyDBServerError(NoxyDBError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
```

- [ ] **Step 4: Implement JSON validation, transport, and handles**

Create `client.py` with the concrete JSON validation, transport, and handle implementation below. `_validate_json_value` removes container identities after walking them, so sharing a non-cyclic list or object in two fields remains valid while active recursion cycles fail.

```python
from __future__ import annotations

import json
import math
import re
import socket
import urllib.error
import urllib.request
from types import TracebackType
from typing import Any

from .errors import NoxyDBConnectionError, NoxyDBServerError, NoxyDBValidationError
from .models import LookupResult, PutResult

_DATABASE_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_MIN_INT64 = -(2**63)
_MAX_INT64 = 2**63 - 1


def _validate_json_value(value: object, active: set[int]) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        if value < _MIN_INT64 or value > _MAX_INT64:
            raise NoxyDBValidationError("integer is outside signed 64-bit range")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NoxyDBValidationError("float must be finite")
        return
    if isinstance(value, (list, dict)):
        identity = id(value)
        if identity in active:
            raise NoxyDBValidationError("circular document")
        active.add(identity)
        try:
            if isinstance(value, list):
                for item in value:
                    _validate_json_value(item, active)
            else:
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise NoxyDBValidationError("document keys must be strings")
                    _validate_json_value(item, active)
        finally:
            active.remove(identity)
        return
    raise NoxyDBValidationError("document is not JSON-compatible")


def _require_bool(response: dict[str, Any], field: str) -> bool:
    value = response.get(field)
    if not isinstance(value, bool):
        raise NoxyDBConnectionError(f"invalid server response: {field}")
    return value


def _require_string(response: dict[str, Any], field: str) -> str:
    value = response.get(field)
    if not isinstance(value, str):
        raise NoxyDBConnectionError(f"invalid server response: {field}")
    return value


class NoxyDBClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765", timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> bool:
        response = self._request("/v1/health", method="GET")
        return _require_bool(response, "success") and response.get("status") == "ok"

    def open_database(self, name: str) -> "Database":
        if not isinstance(name, str) or _DATABASE_NAME.fullmatch(name) is None:
            raise NoxyDBValidationError("invalid database name")
        response = self._request("/v1/open", {"database": name})
        if not _require_bool(response, "success"):
            raise NoxyDBServerError(200, _require_string(response, "error"))
        return Database(self, name)

    def _request(
        self,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        method: str = "POST",
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            try:
                decoded_error = json.loads(error.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as decode_error:
                raise NoxyDBConnectionError("invalid error response") from decode_error
            if not isinstance(decoded_error, dict) or not isinstance(decoded_error.get("error"), str):
                raise NoxyDBConnectionError("invalid error response") from error
            raise NoxyDBServerError(error.code, decoded_error["error"]) from error
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
            raise NoxyDBConnectionError("failed to connect to NoxyDB server") from error
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NoxyDBConnectionError("invalid JSON response") from error
        if not isinstance(decoded, dict):
            raise NoxyDBConnectionError("invalid server response")
        return decoded

class Database:
    def __init__(self, client: NoxyDBClient, name: str) -> None:
        self._client = client
        self.name = name
        self._open = True

    def _ensure_open(self) -> None:
        if not self._open:
            raise NoxyDBValidationError("database handle is closed")

    def _require_key(self, key: str) -> None:
        if not isinstance(key, str):
            raise NoxyDBValidationError("key must be a string")

    def put(self, key: str, value: dict[str, object]) -> PutResult:
        self._ensure_open()
        self._require_key(key)
        if not isinstance(value, dict):
            raise NoxyDBValidationError("document root must be an object")
        _validate_json_value(value, set())
        response = self._client._request(
            "/v1/put", {"database": self.name, "key": key, "value": value}
        )
        return PutResult(_require_bool(response, "success"), _require_string(response, "error"))

    def get(self, key: str) -> LookupResult:
        self._ensure_open()
        self._require_key(key)
        response = self._client._request("/v1/get", {"database": self.name, "key": key})
        found = _require_bool(response, "found")
        value = response.get("value")
        if not isinstance(value, dict):
            raise NoxyDBConnectionError("invalid server response: value")
        return LookupResult(found, value)

    def exists(self, key: str) -> bool:
        self._ensure_open()
        self._require_key(key)
        response = self._client._request("/v1/exists", {"database": self.name, "key": key})
        return _require_bool(response, "exists")

    def remove(self, key: str) -> None:
        self._ensure_open()
        self._require_key(key)
        response = self._client._request("/v1/remove", {"database": self.name, "key": key})
        if not _require_bool(response, "success"):
            raise NoxyDBServerError(200, _require_string(response, "error"))

    def close_database(self) -> None:
        if not self._open:
            return
        response = self._client._request("/v1/close", {"database": self.name})
        if not _require_bool(response, "success"):
            raise NoxyDBServerError(200, _require_string(response, "error"))
        self._open = False

    def close(self) -> None:
        self.close_database()

    def __enter__(self) -> "Database":
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close_database()
```

The implementation sends `Content-Type: application/json` and decodes UTF-8 JSON objects only. It does not catch `KeyboardInterrupt` or `SystemExit`.

Export all public names from `__init__.py` using an explicit `__all__` list.

- [ ] **Step 5: Run unit tests and verify GREEN**

Run:

```powershell
$env:PYTHONPATH = (Resolve-Path "python\src").Path
python -m unittest discover -s python\tests -p "test_client.py" -v
```

Expected: every client unit test passes with no warnings.

- [ ] **Step 6: Verify editable installation**

Run inside a temporary virtual environment:

```powershell
python -m venv .tmp-noxydb-venv
.\.tmp-noxydb-venv\Scripts\python.exe -m pip install --no-deps -e .\python
.\.tmp-noxydb-venv\Scripts\python.exe -c "from noxydb import NoxyDBClient, PutResult, LookupResult; print('python client import passed')"
```

Expected: installation exits zero and prints `python client import passed`. Remove only `.tmp-noxydb-venv` after resolving its absolute path within the repository.

- [ ] **Step 7: Commit the Python client slice**

```powershell
git add .gitignore python/pyproject.toml python/src/noxydb python/tests/test_client.py
git commit -m "feat: add Python client for NoxyDB server"
```

---

### Task 5: Real Server Integration, Persistence, Concurrency, and Documentation

**Files:**
- Create: `python/tests/test_integration.py`
- Modify: `tests/run_tests.ps1`
- Modify: `README.md`

**Interfaces:**
- Consumes: the daemon CLI from Task 3 and public Python API from Task 4.
- Produces: repeatable end-to-end verification and user-facing setup instructions.

- [ ] **Step 1: Write failing integration tests**

Create `python/tests/test_integration.py`. In `setUpClass`, require `NOXY_EXE`, allocate a free localhost port by binding a temporary Python socket to port 0, create `TemporaryDirectory`, start `[NOXY_EXE, server/noxydb_server.nx, --data-dir, tempdir, --port, port]` with repository root as `cwd`, and poll `client.health()` for up to five seconds. In cleanup, terminate the exact child process and wait before killing it only if termination times out.

Implement these tests with the real public client:

```python
def test_crud_unicode_and_complete_replacement(self):
    db = self.client.open_database("usuarios")
    self.assertEqual(db.put("usuário:1", {
        "name": "Estevão",
        "profile": {"city": "Cuiabá"},
        "languages": ["Python", "Noxy"],
        "active": True,
    }), PutResult(True, ""))
    self.assertEqual(db.get("usuário:1").value["profile"]["city"], "Cuiabá")
    db.put("usuário:1", {"name": "Estevão Fonseca"})
    self.assertNotIn("profile", db.get("usuário:1").value)
    db.remove("usuário:1")
    self.assertFalse(db.exists("usuário:1"))

def test_multiple_databases_are_created_and_isolated(self):
    users = self.client.open_database("usuarios")
    orders = self.client.open_database("pedidos")
    users.put("same-key", {"kind": "user"})
    orders.put("same-key", {"kind": "order"})
    self.assertEqual(users.get("same-key").value["kind"], "user")
    self.assertEqual(orders.get("same-key").value["kind"], "order")
    self.assertTrue((self.data_dir / "usuarios.db").exists())
    self.assertTrue((self.data_dir / "pedidos.db").exists())

def test_concurrent_clients_are_serialized_without_lost_documents(self):
    self.client.open_database("parallel")
    def write(index: int) -> None:
        db = NoxyDBClient(self.base_url).open_database("parallel")
        db.put(f"key:{index}", {"index": index})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(40)))
    check = self.client.open_database("parallel")
    self.assertTrue(all(check.get(f"key:{index}").value["index"] == index for index in range(40)))
```

Add these concrete acceptance cases. The fixture helper `_restart_server()` terminates and waits for the current child, starts a new child with the same port and data directory, and polls health again. `_raw_http(parts)` opens one socket, calls `sendall` for each byte part, shuts down its write side, reads until EOF, and returns the complete response bytes.

```python
def test_persistence_after_daemon_restart(self):
    db = self.client.open_database("persistent")
    db.put("key", {"value": "survives"})
    self._restart_server()
    reopened = self.client.open_database("persistent")
    self.assertEqual(reopened.get("key").value, {"value": "survives"})

def test_fragmented_http_request_is_assembled(self):
    response = self._raw_http([
        b"POST /v1/open HTTP/1.1\r\nHost: 127.0.0.1\r\n",
        b"Content-Length: 25\r\n\r\n{\"database\":",
        b"\"fragmented\"}",
    ])
    self.assertTrue(response.startswith(b"HTTP/1.1 200 OK\r\n"))
    self.assertIn(b'{"success":true', response)

def test_declared_request_over_one_mib_is_rejected(self):
    response = self._raw_http([
        b"POST /v1/put HTTP/1.1\r\nHost: 127.0.0.1\r\n",
        b"Content-Length: 1048577\r\n\r\n",
    ])
    self.assertTrue(response.startswith(b"HTTP/1.1 400 Bad Request\r\n"))

def test_server_rejects_invalid_database_name(self):
    with self.assertRaises(NoxyDBServerError) as raised:
        self.client._request("/v1/open", {"database": "../outside"})
    self.assertEqual(raised.exception.status, 400)

def test_invalid_log_error_does_not_expose_data_path(self):
    invalid_path = self.data_dir / "broken.db"
    invalid_path.write_bytes(b"P\t00")
    with self.assertRaises(NoxyDBServerError) as raised:
        self.client.open_database("broken")
    self.assertEqual(raised.exception.status, 500)
    self.assertNotIn(str(self.data_dir), str(raised.exception))
```

- [ ] **Step 2: Run the acceptance tests and capture the actual result**

Run:

```powershell
$env:NOXY_EXE = "D:\OneDrive\Documentos\go_projects\noxy\noxy.exe"
$env:PYTHONPATH = (Resolve-Path "python\src").Path
python -m unittest discover -s python\tests -p "test_integration.py" -v
```

Expected: all tests pass if the earlier test-driven slices compose correctly. Any failure is a demonstrated integration defect and must enter a fresh red-green cycle in Step 3.

- [ ] **Step 3: Fix only demonstrated integration defects with a regression RED-GREEN cycle**

For each failure, first reduce it to the smallest failing test, confirm that focused test fails for the observed reason, and then change the owning module rather than weakening the assertion:

- framing or status defects: `server/http_transport.nx`;
- route/request defects: `server/protocol.nx`;
- ordering, caching, or persistence defects: `server/database_worker.nx`;
- response/exception decoding defects: `python/src/noxydb/client.py`.

Keep the public interfaces and status mapping from the approved specification unchanged. Re-run the single failing test after each correction, then the complete integration file.

- [ ] **Step 4: Extend the PowerShell runner**

Move the existing `NOXY_EXE` validation below the Python-only branch shown first. This lets client unit tests run without Noxy installed:

```powershell
$pythonPath = Join-Path $projectRoot "python\src"

if ($Group -eq "python") {
    $env:PYTHONPATH = $pythonPath
    & python -m unittest discover -s (Join-Path $projectRoot "python\tests") -p "test_client.py" -v
    if ($LASTEXITCODE -ne 0) { throw "Python client tests failed" }
    Write-Output "All Python client tests passed."
    exit 0
}

```

Keep the existing non-empty and file checks for `$noxyExe` immediately after that branch. Then add the process-spawning integration branch, which now runs only after the executable has been validated:

```powershell
if ($Group -eq "integration") {
    $env:PYTHONPATH = $pythonPath
    & python -m unittest discover -s (Join-Path $projectRoot "python\tests") -p "test_integration.py" -v
    if ($LASTEXITCODE -ne 0) { throw "NoxyDB integration tests failed" }
    Write-Output "All NoxyDB integration tests passed."
    exit 0
}
```

After the existing Noxy loop, run the default Python unit suite only when `$Test` and `$Group` are both empty:

```powershell
if ([string]::IsNullOrWhiteSpace($Test) -and [string]::IsNullOrWhiteSpace($Group)) {
    $env:PYTHONPATH = $pythonPath
    & python -m unittest discover -s (Join-Path $projectRoot "python\tests") -p "test_client.py" -v
    if ($LASTEXITCODE -ne 0) { throw "Python client tests failed" }
}
```

- [ ] **Step 5: Document the completed workflow**

Add a `NoxyDB Server` section to `README.md` containing these exact user flows:

```powershell
# Start a persistent local server, even when no database exists yet
& "D:\path\to\noxy.exe" server\noxydb_server.nx --data-dir .\data --port 8765

# Install the Python client during development
python -m pip install -e .\python
```

```python
from noxydb import NoxyDBClient

client = NoxyDBClient("http://127.0.0.1:8765")
db = client.open_database("usuarios")
db.put("user:1", {"name": "Estevão", "active": True})

result = db.get("user:1")
if result.found:
    print(result.value["name"])

db.remove("user:1")
db.close()
```

Explain that the server creates `data/usuarios.db` on first open, remains running without clients, manages multiple names, has no tables or collections, accepts only localhost connections, has no authentication because it is local-only, and must not share its `.db` files with another NoxyDB process concurrently.

- [ ] **Step 6: Run fresh final verification**

Run all commands from the repository root:

```powershell
$env:NOXY_EXE = "D:\OneDrive\Documentos\go_projects\noxy\noxy.exe"
.\tests\run_tests.ps1
.\tests\run_tests.ps1 -Group integration
python -m compileall -q python\src python\tests
git diff --check
git status --short
```

Expected: both test commands exit zero, `compileall` exits zero without output, `git diff --check` prints nothing, and `git status --short` lists only intended feature changes plus the preserved unrelated `?? cadastro_usuarios.nx`.

- [ ] **Step 7: Commit integration and documentation**

```powershell
git add python/tests/test_integration.py tests/run_tests.ps1 README.md server python/src/noxydb python/pyproject.toml python/tests/test_client.py
git commit -m "feat: complete local NoxyDB server workflow"
```

Before committing, inspect `git diff --cached --stat` and confirm that `cadastro_usuarios.nx` is not staged.

---

## Completion Checklist

- [ ] The daemon starts with an empty or newly created data directory and stays alive without clients.
- [ ] The daemon binds only `127.0.0.1` and supports several named databases.
- [ ] Database files are created implicitly by `open_database` and are isolated by validated logical name.
- [ ] HTTP parsing handles fragmentation, exact byte lengths, malformed input, and the 1 MiB limit.
- [ ] A single Noxy worker owns and serializes all database access.
- [ ] The Python client mirrors open/put/get/exists/remove/close with documented results and exceptions.
- [ ] Existing embedded NoxyDB behavior and v0.2 persistence tests remain green.
- [ ] Unit, integration, concurrency, restart, Unicode, invalid-log, and packaging checks pass.
- [ ] README instructions reproduce server startup and first-use database creation.
- [ ] No Noxy VM files or unrelated workspace files are modified.
