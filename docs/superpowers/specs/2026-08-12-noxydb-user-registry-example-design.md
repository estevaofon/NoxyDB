# NoxyDB User Registry Example Design

## Goal

Port the interactive SQLite example `noxy_examples/cadastro_usuarios.nx` to the
current NoxyDB document API while preserving its add, list, remove, update, and
exit workflow.

## Files

- Add `examples/cadastro_usuarios.nx`.
- Update `README.md` to reference the interactive example.
- Keep the existing `examples/documents.nx` walkthrough unchanged.
- Use `examples/usuarios.db`, already covered by `examples/*.db` in
  `.gitignore`.

## Data model

Each user is stored under `usuario:<id>` as a JSON document:

```json
{
  "id": 1,
  "nome": "Estevão",
  "email": "estevao@example.com",
  "cargo": "Desenvolvedor"
}
```

Because NoxyDB does not scan keys, `usuarios:meta` provides the explicit index:

```json
{
  "next_id": 2,
  "ids": [1]
}
```

The metadata document is created lazily on the first run and persists between
executions. IDs are monotonically increasing and are not reused after removal.

## Operations

- Add: load metadata, store `usuario:<next_id>`, append the ID, increment
  `next_id`, and replace `usuarios:meta`.
- List: load metadata, iterate `ids`, and fetch each `usuario:<id>` document.
- Remove: require that the user key exists, delete it, rebuild `ids` without the
  removed ID, and replace `usuarios:meta`.
- Update: require that the user exists and replace its complete document while
  preserving the selected ID.
- Exit: close the database and surface a close error if present.

Every `put()` checks `PutResult`; lookups check `LookupResult`. If the user write
succeeds but the following metadata write fails, the program reports the
failure and exits because NoxyDB does not provide transactions.

## Interaction

Preserve the original Portuguese menu and prompts. Invalid menu options print a
short message. Listing an empty registry prints that no users are registered.
The program clears the terminal when supported but does not depend on clearing
successfully.

## Validation

- Drive the menu non-interactively through add, list, update, remove, and exit.
- Run a second process against the same database to prove persistence/replay.
- Confirm `examples/usuarios.db` exists and is ignored by Git.
- Run the full NoxyDB suite and `git diff --check`.
