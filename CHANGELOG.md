# Changelog

## [0.2.1] - 2026-08-14

### Changed

- Streamlined the README around project onboarding, quickstarts, examples, and
  essential operational guidance, with implementation details linked from the
  existing deep-dive document. `#docs` @estevaofon

## [0.2.0] - 2026-08-12

### Added

- Document-valued database API using JSON objects represented as
  `map[string, any]`. `#database` @estevaofon
- Strict document codec, replay validation, isolation guarantees, and coverage
  for nested JSON values, Unicode, overwrite, deletion, persistence, and I/O
  failures. `#database` @estevaofon

### Changed

- The authoritative in-memory state now stores serialized JSON payloads while
  the storage engine remains payload-opaque. `#storage` @estevaofon
- NoxyDB v0.2 deliberately replaces the v0.1 string-value API and physical
  contract without migration or fallback logic. `#database` @estevaofon
