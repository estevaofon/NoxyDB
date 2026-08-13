# Python User Registry Design

## Goal

Port `examples/cadastro_usuarios.nx` faithfully to Python now that Python
applications can access NoxyDB through the local server.

The port must preserve the original interactive behavior, messages, data
layout, and operation order. Its only functional change is replacing the
embedded database file with the Python HTTP client and the logical database
name `usuarios`.

## Scope

Create `examples/cadastro_usuarios.py` as a direct, single-file translation of
the Noxy example. Keep `examples/cadastro_usuarios.nx` unchanged as the
embedded-API counterpart and reference implementation.

The Python example connects to the existing server at the client default URL,
`http://127.0.0.1:8765`, and opens the logical database `usuarios`. It does not
start, configure, or manage the NoxyDB server process.

The port does not add input validation, command-line options, environment
configuration, transactions, domain classes, or a separate application
package.

## Program Structure

The script retains direct function equivalents for the Noxy implementation:

- `fail` prints an `Erro: ...` message, closes the database handle, and exits
  with status 1;
- `require_put` converts an unsuccessful `PutResult` into the same fatal flow;
- `user_key` maps an integer ID to `usuario:<id>`;
- `new_metadata`, `load_metadata`, and `save_metadata` manage the registry
  index;
- `add_user`, `list_users`, `remove_user`, and `update_user` implement the
  four menu actions;
- `main` connects to the server, opens the database, initializes metadata,
  runs the menu, and closes the handle.

The executable call is protected by `if __name__ == "__main__"` so tests can
import the functions without starting the interactive loop. This does not
change behavior when the file is executed normally.

## Data Model and Operation Order

The metadata document remains under the key `usuarios:meta`:

```json
{"next_id": 1, "ids": []}
```

Every user remains a separate document under `usuario:<id>`:

```json
{"id": 1, "nome": "Nome", "email": "email@example.com", "cargo": "Cargo"}
```

Adding a user writes the user document first and then writes the updated
metadata document. These remain two separate writes because NoxyDB does not
provide transactions. Removing a user deletes its document before filtering
the ID from metadata. Updating a user replaces the complete user document.
IDs are monotonic and are not reused after deletion.

Listing follows the order stored in the metadata `ids` array. Missing user
documents referenced by the index are skipped, matching the Noxy example.

## Interactive Behavior

The menu, prompts, success messages, missing-user messages, and invalid-option
message remain in Portuguese and retain the wording of the Noxy script. The
screen is cleared with `cls` before each menu display, matching its Windows
behavior. Listing waits for Enter before returning to the menu; the other
operations do not add a pause.

Choosing option `5` closes the database handle and prints `Sair`.

## Error Handling

The Python client represents transport and server failures with `NoxyDBError`
subclasses rather than the embedded API's database error state. The port maps
those exceptions at each equivalent operation boundary to the Noxy example's
Portuguese context, prints them through `fail`, and exits with status 1.

An unsuccessful `put` remains represented by `PutResult`; `require_put`
reports its `error` text. Opening and final closing failures use the original
`Erro ao abrir banco: ...` and `Erro ao fechar banco: ...` prefixes.

The failure path makes a best-effort logical close after printing the primary
error. A close failure during that cleanup must not replace the original
message. The normal close path reports its own failure and exits with status
1.

## Testing

Add focused tests under `python/tests/test_cadastro_usuarios.py`. They import
the example without entering the menu and exercise the real registry
functions with a small in-memory database test double and controlled console
input/output.

Tests cover:

- the initial metadata shape and `usuario:<id>` key format;
- lazy creation and retrieval of metadata;
- adding a user and advancing the monotonic ID;
- listing in metadata order, including the empty registry and a stale ID;
- removing an existing user and preserving `next_id`;
- reporting an absent user without prompting for replacement fields;
- completely replacing a user during update;
- converting failed writes into the fatal error flow;
- importing the module without running the interactive menu.

Existing Python client tests continue to validate HTTP serialization and
server integration. The new tests focus only on the ported example's registry
and interaction behavior.

## Documentation

Update the README interactive registry section to show that the server must be
running and that the Python port is executed with:

```powershell
python examples/cadastro_usuarios.py
```

The documentation keeps the Noxy invocation available so readers can compare
the embedded and server-backed examples.
