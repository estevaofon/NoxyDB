# Changelog

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
