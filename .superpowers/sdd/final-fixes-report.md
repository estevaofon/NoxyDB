# Final Review Fixes Report

Date: 2026-08-13
Branch: `feature/noxydb-server-python-client`
Starting HEAD: `9b7522a3051d92a6a07f70382cdf1573ba28c3e4`
Implementation commit: `57b1400` (`fix: harden server transport and client validation`)
Noxy executable: `C:\Users\estev\go\bin\noxy.exe`

## Scope

Implemented the final review correction wave without adding a shutdown route,
changing the Noxy VM, or modifying `examples/cadastro_usuarios.nx`.

## Findings and changes

### 1. Incomplete client timeout and socket cleanup

- Added a 1,000 ms read-idle timeout plus a finite size-based absolute
  deadline (1,000 ms + 1 ms per 32 remaining request bytes).
- `net_select`/`net.socket_recv` are the only Noxy network APIs used. The
  handler sends a JSON `400 Bad Request` with `request read timeout` and closes
  the socket.
- The installed runtime stores poll-peek bytes in an internal map that crashes
  on concurrent `net_select` calls. The server therefore serializes each
  poll-and-receive batch with a channel semaphore and uses 10 ms poll slices,
  without changing the VM. The existing concurrent E2E test reproduced the
  runtime crash before this server-side serialization and passes afterward.

### 2. Fragmented surplus boundary

- Request assembly drains bytes immediately available to the current handler.
  Any bytes beyond the declared body in that batch are rejected as `surplus
  request body`.
- After exactly completing a body, the server performs one immediate
  `net_select` probe before routing. A raw-socket segmented E2E test sends a
  complete open request followed by pipelined bytes, asserts HTTP 400, and
  asserts that the database was not created (routing did not occur).
- Precise limitation: bytes that arrive only after the immediate post-body
  probe are not detectable without waiting for EOF or adding a grace wait to
  every valid request. The server intentionally does neither because HTTP
  clients wait for the response. It closes the one-request connection, so such
  later bytes are discarded. This boundary is documented in `README.md`.

### 3. Python lookup response validation

- `Database.get()` recursively validates returned documents using the same
  JSON, finite-float, string-key, cycle, and signed-int64 domain used for
  outbound documents.
- A server document outside that domain becomes `NoxyDBConnectionError`; the
  client does not leak the local-input `NoxyDBValidationError` abstraction.
- Unit tests cover a nested integer above signed int64 and an isolated
  surrogate returned by the server.

### 4. Minor findings

- Exactly 1 MiB without `\r\n\r\n` is `incomplete request`, not `request too
  large`.
- `Content-Length` leading zeros are normalized before the size check, so the
  numeric value controls acceptance/rejection.
- Isolated surrogates in document strings, object keys, or operation keys
  become `NoxyDBValidationError` before UTF-8 encoding.
- The database-worker test uses a timestamp-suffixed directory and performs
  file/directory cleanup before assertions, then asserts cleanup success. This
  is the strongest guaranteed cleanup available without Noxy exceptions or a
  `finally` construct.
- `README.md` distinguishes embedded physical close from remote logical close.
- `README.md` states that the existing cleanup runs only if `serve_local`
  returns; Ctrl-C/process termination is abrupt and does not physically close
  cached handles. No shutdown protocol or signal handling was added.

## TDD evidence

Observed RED before production changes:

- `http_transport_test.nx`: leading-zero `Content-Length: 000000023` was
  rejected by textual length; the test failed at "leading-zero length within
  the limit should be accepted".
- `ClientTests.test_rejects_isolated_surrogates_as_validation_errors`: both
  document cases raised raw `UnicodeEncodeError`.
- `ClientTests.test_lookup_rejects_documents_outside_the_noxy_json_domain`:
  both invalid server documents were accepted.
- `IntegrationTests.test_incomplete_client_does_not_block_other_clients`:
  stalled socket timed out without a server response/close.
- `IntegrationTests.test_fragmented_surplus_is_rejected_before_routing`:
  response was not HTTP 400 before the immediate surplus probe.
- Full E2E concurrency then reproduced `fatal error: concurrent map writes` in
  the runtime poll buffer; the existing concurrent-client test became the RED
  regression for the server-side poll semaphore.

Focused GREEN evidence:

```text
tests/run_tests.ps1 -Group server
All NoxyDB tests passed (3 files).

ClientTests.test_rejects_isolated_surrogates_as_validation_errors
ClientTests.test_lookup_rejects_documents_outside_the_noxy_json_domain
Ran 2 tests ... OK

IntegrationTests.test_concurrent_clients_are_serialized_without_lost_documents
IntegrationTests.test_incomplete_client_does_not_block_other_clients
IntegrationTests.test_fragmented_surplus_is_rejected_before_routing
Ran 3 tests ... OK
```

## Final verification

All commands were run fresh after the final implementation:

```powershell
$env:NOXY_EXE = 'C:\Users\estev\go\bin\noxy.exe'
.\tests\run_tests.ps1
# All NoxyDB tests passed (19 files).
# Python client: Ran 31 tests ... OK

.\tests\run_tests.ps1 -Group python
# Ran 31 tests ... OK
# All Python client tests passed.

.\tests\run_tests.ps1 -Group integration
# Ran 10 tests ... OK
# All NoxyDB integration tests passed.

python -m compileall -q python\src python\tests
# exit 0

git diff --check
# exit 0 (only Git line-ending/config warnings)
```

Scope checks:

- `git diff --name-only` lists only README, Python client/tests, server HTTP
  transport, and Noxy server tests plus this report.
- No path matching the VM or `cadastro_usuarios.nx` appears in the current
  diff or in `c7f69a3...HEAD`.
- No `tests/tmp_server_worker*` directory remains after verification.

## Remaining limitation

Perfect detection of bytes arriving after the one immediate post-body probe is
impossible for this HTTP stream contract without EOF or a grace wait that
delays every request. The implemented behavior detects all surplus bytes
already available at completion/probe time, rejects them before routing, and
documents the exact temporal boundary.
