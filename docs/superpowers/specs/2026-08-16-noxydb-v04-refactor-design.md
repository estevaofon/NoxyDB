# NoxyDB v0.3.0 sobre Noxy v0.4.0 — design

**Data:** 2026-08-16
**Estado:** aprovado, pronto para plano de implementação
**Versão alvo do NoxyDB:** 0.3.0 (hoje 0.2.0)
**Versão alvo do Noxy:** 0.4.0 (o `noxy.mod` declara `v0.1.0`, desatualizado)

---

## 1. Contexto

O Noxy v0.4.0 trocou a semântica de vínculo de arrays, maps e structs para
valor com copy-on-write. `ref` passou a ser o único mecanismo de
compartilhamento da linguagem. O NoxyDB foi escrito sob a semântica anterior e
depende de mutação através de parâmetro em todo o seu núcleo.

Além disso, o v0.3.0 introduziu mudanças que o projeto nunca absorveu, e
resolveu três limitações que o servidor do NoxyDB contornou à mão.

### Estado medido antes do refactor

Suíte rodada contra `C:\Users\estev\go\bin\noxy.exe` (Noxy v0.4.0): **8 de 19
testes `.nx` falham**.

Os testes de persistência passam porque a escrita **no log** nunca quebrou:
`storage.append_put` recebe `io.File` por valor e não muta nada. O que se perde
é só a atualização do mapa em memória. Por isso `put` seguido de `get` no mesmo
processo falha, enquanto `put` seguido de reabertura funciona.

### Causas das falhas

| # | Causa | Onde |
|---|---|---|
| 1 | Mutação através de parâmetro não-`ref` | `noxydb.nx` (`put`, `remove`, `fail_database`, `close_database`), `database_worker.nx:34` |
| 2 | `to_str` levanta em bytes não-UTF-8 (v0.3.0) | `storage.nx:26` |
| 3 | `map` declarado sem inicializador atravessando módulo | `noxydb.nx:97` |
| 4 | `substring` com terceiro argumento como comprimento (v0.3.0) | `http_transport.nx:148` |

### Por que o código antigo funcionava

Antes do v0.4.0, compostos eram passados por **cópia rasa**: `f(db)` copiava o
struct externo, mas `db.state.payloads` continuava sendo o mesmo mapa, então a
mutação através do parâmetro era visível no chamador. Isso estava registrado
como decisão deliberada da linguagem (ponto 11 do inventário da §2.5).

O v0.4.0 trocou cópia rasa por valor profundo com CoW, e é exatamente aí que o
NoxyDB quebra: `db.state.payloads` passou a ser independente em qualquer
profundidade.

---

## 2. Evidência medida

Todas as decisões abaixo se apoiam em medição, não em leitura de spec. Os
números foram obtidos nesta máquina, Noxy v0.4.0 pós-commit `9ada148`.

### 2.1 Passagem por valor é uma armadilha de performance

Laço de N=5000 alternando uma leitura e uma escrita sobre o mesmo `Database`:

| padrão | tempo | escala |
|---|---:|---|
| só `put` | 12 ms | linear |
| leitura com `ref` + `put` | 16 ms | linear |
| leitura **por valor** + `put` | **5.096 ms** | quadrática |

O mesmo vale para uma função que lê **apenas um campo escalar** (`db.state.n`):
5.198 ms por valor contra 15 ms por `ref`. A marcação *Shared* acontece no
vínculo do parâmetro, independente do que o corpo toca.

**Consequência de design:** toda função pública recebe `ref Database`,
inclusive as só-leitura. Uma API mista seria uma mina de 300x.

### 2.2 Representação do estado em memória

50k documentos de 147 bytes, pico de working set já descontado o baseline de
7,2 MB do interpretador:

| representação | 20k leituras | memória |
|---|---:|---:|
| `map[string, string]` (JSON serializado) | 199 ms | **17,2 MB** |
| `map[string, map[string, any]]` (parseado) | 61 ms | **158,4 MB** |

**Decisão: manter `map[string, string]`.** O NoxyDB carrega o dataset inteiro
em RAM, então memória é o teto de escala do projeto. Trocar 9x de memória por
3,3x de leitura derruba o tamanho máximo de banco em quase uma ordem de
grandeza.

Isso **não** é manter o status quo por inércia: a justificativa muda de dona.
Serializar deixou de ser o que garante isolamento — o CoW garante sozinho.
Passa a ser o que garante densidade de memória.

### 2.3 Fidelidade do round-trip JSON

Descartou-se guardar o map do chamador direto, porque o round-trip não é fiel
para todos os tipos:

```
encoded:    {"neg_zero":-0,"tiny":0.1,"whole_float":1}
re-encoded: {"neg_zero":0,"tiny":0.1,"whole_float":1}
```

Um `1.0` gravado volta como int `1` depois de um restart. Inteiros comuns e
inteiros grandes (`9007199254740993`) sobrevivem intactos.

### 2.4 Capacidades novas do runtime, verificadas

| Capacidade | Verificação |
|---|---|
| `sys.signal_notify(ch)` entrega SIGINT/SIGTERM | Ctrl-C → sinal `2` → limpeza executada → saída limpa |
| `http_server` da stdlib serve com worker por canal | servidor real dirigido de ponta a ponta, round trip via canal |
| `response_json` + override de `status_code` | 404 devolvido como `application/json` |
| Erros de transporte da stdlib | chunked → `501 Not Implemented`, `text/plain` |
| `stop_server` encerra `serve` | processo saiu limpo após a rota de shutdown |
| `ref` para dentro de map | `ref cache["a"]` muta a entrada em cache |
| `substring` com terceiro arg exclusivo | `substring("\"abc\"", 1, 4)` → `abc`; com `length-2` → `ab` |
| `spawn_task` + `task_await` supervisionam | worker vivo → `timeout`; encerrado → `ok`; falho → `error` com `kind="runtime"` e mensagem com arquivo:linha; await repetido devolve o mesmo desfecho |

### 2.5 Proveniência: as 13 limitações reportadas

Ao construir o NoxyDB foram reportadas 13 limitações da linguagem, catalogadas
nos comentários do [PR #17](https://github.com/estevaofon/noxy/pull/17). Todas
foram corrigidas e mergeadas. O mapeamento para este refactor:

| # | Limitação | Corrigida em | Efeito aqui |
|---|---|---|---|
| 1 | `net_select` inseguro para chamadas concorrentes | #17 | remove o semáforo `network_io` (§6.1) |
| 2 | `spawn` perde globals do módulo | #17 | habilita o handler ler `commands` de um `let` de topo (§6.3) |
| 3 | Erros em rotina spawnada não propagam | #19 | worker supervisionado por `spawn_task` (§5.2) |
| 4 | `net_setblocking` no-op | #20 | deadlines reais, absorvidos pelo `http_server` |
| 5 | `net_select` consome byte para testar legibilidade | #21 | remove o `socket_recv(client, 1)` (§6.1) |
| 6 | timeout zero vira 1 ms | #21 | remove a sonda de surplus (§6.1) |
| 7 | conjuntos de escrita/erro ignorados | #21 | idem |
| 8 | não é multiplexador real | #21 | idem |
| 9 | Sem tratamento de sinais | #22 | encerramento por sinal (§7.1) |
| 10 | Sem `defer`/`finally` | #18 | cleanup garantido nos testes (§9.4) |
| 11 | Mapas passados por valor (cópia rasa) | superado pelo v0.4.0 | ver §1, "Por que o código antigo funcionava" |
| 12 | `substring` inconsistente | v0.3.0 | corrige `escaped_log_field` (§4.3) |
| 13 | Servidor HTTP lê um chunk só | #26 | substitui o transporte artesanal (§6) |

Sete linhas do transporte artesanal existiam por causa de 1, 5, 6, 7, 8 e 13.
Removê-lo não é preferência de estilo: é retirar código cuja razão de existir
acabou.

---

## 3. Contrato público do núcleo

### 3.1 Assinaturas

```noxy
func open_database(path: string) -> Database          // devolve valor, não muta nada
func put(db: ref Database, key: string, value: map[string, any]) -> PutResult
func get(db: ref Database, key: string) -> LookupResult
func remove(db: ref Database, key: string) -> void
func exists(db: ref Database, key: string) -> bool
func is_open(db: ref Database) -> bool
func database_error(db: ref Database) -> string
func close_database(db: ref Database) -> void
```

`open_database` é a única que não recebe `ref`: ela constrói e devolve um
`Database` novo.

`get` recebe `ref` por dois motivos independentes: falha o banco ao encontrar
payload corrompido (mutação real), e por valor cairia na armadilha de 2.1.

`remove` mantém `-> void` com erro lido por `database_error`. Alinhar com
`PutResult` seria uma melhoria, mas está fora do que o CoW obriga e não entra.

### 3.2 Regra de call site

Chamada qualificada de módulo (`noxydb.put(...)`) é fronteira dinâmica: o
compilador não conhece a assinatura exata, então **`ref` é obrigatório e
explícito**. Sem ele, o runtime rejeita com
`expected ref Database, got object`.

```noxy
let db: noxydb.Database = noxydb.open_database("database.db")
let stored: noxydb.PutResult = noxydb.put(ref db, "user:1", user)
let result: noxydb.LookupResult = noxydb.get(ref db, "user:1")
noxydb.close_database(ref db)
```

**Pegadinha a documentar:** mesmo quando a variável **já tem** tipo
`ref Database`, a chamada de módulo exige `ref` de novo para encaminhar a
referência existente (REF_SEMANTICS §2). Isto falha:

```noxy
let handle: ref noxydb.Database = ref cache["a"]
noxydb.put(handle, "k", v)        // erro: expected ref Database, got object
noxydb.put(ref handle, "k", v)    // correto
```

Isto é breaking em 100% dos call sites existentes e não tem alternativa que
preserve performance.

### 3.3 Structs

`LookupResult`, `PutResult`, `DatabaseState` e `Database` mantêm os campos
atuais. `DatabaseState.payloads` segue `map[string, string]`.

---

## 4. Correções não relacionadas a CoW

### 4.1 `storage.nx` — decodificação de hex não-UTF-8

`decode_hex` faz `to_str(hex_decode(value))`. Desde o v0.3.0, `to_str` **levanta**
sobre bytes que não sejam UTF-8 válido, então um log corrompido derruba o
processo em vez de virar um erro de replay.

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

`replay` checa `.ok` e devolve `ReplayResult(false, payloads, "invalid database
log record")`.

Um log bem formado sempre decodifica: chaves são strings Noxy (UTF-8 válido por
invariante do v0.3.0) e payloads são JSON. Só um log corrompido ou forjado
atinge esse caminho.

### 4.2 `noxydb.nx` — map sem inicializador

`let empty: map[string, any]` atravessando a fronteira de módulo produz um valor
sem metadado de runtime; indexar o `value` de um `LookupResult` não-encontrado
levanta `runtime value metadata conflicts with static context`. Passa a ser
`let empty: map[string, any] = {}`.

O mesmo padrão aparece em `failed_database` e em `storage.replay`; todos os
`let <nome>: map[...]` sem inicializador recebem `= {}`.

### 4.3 `substring` — terceiro argumento é índice final exclusivo

O v0.3.0 unificou a semântica de `substring` (ponto 12 do inventário). O
terceiro argumento é um **índice final exclusivo**, não um comprimento.

`escaped_log_field` assume a semântica antiga e come o último caractere:

```noxy
// atual — devolve "ab" para a entrada "\"abc\""
return strings.substring(encoded.data, 1, length(encoded.data) - 2)

// correto — devolve "abc"
return strings.substring(encoded.data, 1, length(encoded.data) - 1)
```

Esta é a causa da falha de `http_transport_test.nx` ("activity fields should
escape control characters"). A função migra para `api.nx` (§6.2) já corrigida.

**Os demais usos em produção foram auditados e estão corretos**, porque usam a
forma `(s, start, length(s))` ou `(s, 0, n)`, que produz o mesmo resultado nas
duas semânticas: `http_transport.nx:93,97,104` e `noxydb_server.nx:31`. Só a
linha 148 estava errada.

---

## 5. Worker de banco

### 5.1 Acesso à entrada em cache

`execute_database_command` e `close_all_databases` já recebem
`ref map[string, noxydb.Database]`. O que muda é o acesso à entrada:

```noxy
// antes — copia a entrada; a mutação se perde
let db: noxydb.Database = databases[command.database]

// depois — liga ao slot do map
let handle: ref noxydb.Database = ref databases[command.database]
```

Todas as chamadas subsequentes usam `ref handle`, conforme 3.2.

A arquitetura de comando por canal **não muda**. Verificado que um struct
contendo um canal (`DatabaseCommand.response`) sobrevive à entrega por valor do
`chan_send`: o canal é um handle, não um composto clonado.

### 5.2 Supervisão do worker

Hoje o worker sobe com `spawn` solto. Se ele levantar um erro de runtime,
ninguém observa: cada request seguinte bloqueia para sempre em
`chan_recv(reply)`, porque não há mais quem responda. O cliente vê uma conexão
pendurada até o próprio timeout, nunca um erro — o pior modo de falha possível
para um servidor de banco.

O worker passa a subir com `spawn_task`, que devolve um handle opaco:

```noxy
let worker_task: any = spawn_task(worker.run_database_worker, commands, worker_done, ...)
```

Uma rotina supervisora faz `task_await(worker_task, timeout)` em laço. O
envelope devolvido é um `map[string, any]` com `status` em `"ok"`, `"error"` ou
`"timeout"`; timeout é não-terminal e não cancela o worker. Ao observar
`"error"`, a supervisora imprime `kind`, `message` e `stack` do mapa de falha e
inicia o mesmo caminho de encerramento da §7.2.

O resultado é falhar alto e cedo, com diagnóstico, em vez de pendurar em
silêncio. `task_await` sobre um worker que terminou normalmente devolve `"ok"`,
que é o caso do encerramento limpo e não deve emitir diagnóstico de erro.

---

## 6. Servidor HTTP

`server/http_transport.nx` (328 linhas) é **removido** e substituído por
`server/api.nx` (~120 linhas) sobre o `http_server` da stdlib.

### 6.1 O que sai

Semáforo `network_io`; `net.socket_recv(client, 1)` byte a byte; deadline
artesanal (1.000 ms + 1 ms por 32 bytes); sonda de surplus pós-corpo;
`assemble_request`; `find_header_end`; `decimal_length`; `build_http_response`;
`reason_phrase`; `poll_receive`; `has_immediate_surplus`; `read_http_request`;
`next_receive_size`; `serve_local`.

Todos existiam para contornar limitações que a linguagem não tem mais.

### 6.2 O que fica, movido para `api.nx`

`operation_for`, `build_activity_line`, `escaped_log_field`,
`quoted_log_value`, e o roteamento para o worker por canal.

### 6.3 Forma do handler

```noxy
let commands: chan any = make_chan(64)      // let de topo, lido pelo handler
let server: HttpServer = new_server("127.0.0.1", port)

func handler(req: HttpRequest) -> HttpResponse
    // roteia, envia comando ao worker, espera resposta, monta HttpResponse
end
```

O handler é `func handler(req: HttpRequest) -> HttpResponse` e alcança o canal
por um `let` de topo — padrão documentado em `HTTP_SERVER.md` e verificado com
um servidor real.

`serve` faz `spawn` por conexão. A serialização por worker único continua sendo
o que protege o arquivo `.db`; o semáforo de rede é que era supérfluo.

### 6.4 Contrato HTTP

| classe | corpo | códigos |
|---|---|---|
| Sucesso e erro de aplicação | `application/json` | 200, 400, 404, 405, 409, 500 |
| Erro de transporte (gerado pela stdlib antes do handler) | `text/plain` | 400, 408, 413, 414, 431, 501, 505 |

Erros de aplicação preservam JSON via `response_json` com override de
`status_code` e `status_text`. Erros de transporte não são customizáveis — a
stdlib os gera antes do handler rodar.

**400 aparece nas duas classes e não é ambiguidade de escrita.** Um corpo JSON
inválido numa rota válida é erro de aplicação e volta como JSON; um request
malformado no nível do quadro (linha de request inválida, header com caractere
de controle, `Content-Length` duplicado) é erro de transporte e volta como
`text/plain`. Cliente e testes distinguem pelo `Content-Type`, nunca pelo
código sozinho.

**Mudança de comportamento em surplus.** O servidor atual rejeita com 400 os
bytes que chegam além do corpo declarado. A stdlib **descarta** esses bytes,
porque fecha a conexão após uma resposta (`HTTP_SERVER.md`, "Framing
contract"). Não é configurável e não há como reproduzir a rejeição sem
reescrever o framing — o que é exatamente o que este refactor remove. Isto é
uma perda deliberada de rigor em troca de 328 linhas de transporte artesanal.
Ver §9.2 para o efeito nos testes.

### 6.5 Limites

`max_body_bytes` é fixado em 1.048.576 para preservar o limite de 1 MiB de hoje.
Os demais campos (`max_header_bytes`, `header_timeout_ms`, `body_timeout_ms`,
`write_timeout_ms`, `read_chunk_bytes`) ficam nos defaults da stdlib.

---

## 7. Encerramento

Dois gatilhos convergindo num único caminho.

### 7.1 Gatilhos

1. **Sinais.** `sys.signal_notify(ch)` para SIGINT e SIGTERM. Uma rotina
   dedicada espera na channel e inicia o encerramento.
2. **Rota `POST /v1/shutdown`**, existente **apenas** com `--enable-shutdown`.
   Sem a flag, responde 404 como qualquer caminho desconhecido.

### 7.2 Sequência

```
gatilho → stop_server(ref server) → serve retorna
        → chan_close(commands)
        → worker fecha fisicamente todo .db em cache
        → worker confirma pelo canal de handshake
        → processo sai
```

**O handshake é obrigatório.** No primeiro spike, sem ele, o processo saiu antes
de o worker rodar o fechamento: o encerramento "limpo" não fechava nada. O
`main` faz `chan_recv(worker_done)` depois do `chan_close(commands)`.

`stop_server` precisa ser chamado de uma rotina diferente da que roda `serve`.
O handler de conexão e a rotina de sinais satisfazem isso naturalmente.

### 7.3 Efeito na documentação

O README afirma hoje que "the current Noxy runtime exposes no signal handling to
this server". Isso ficou falso e sai. Termination abrupta por Task Manager /
`kill -9` continua fora de alcance e permanece documentada.

---

## 8. Cliente Python

- `_request` passa a aceitar corpo de erro `text/plain`, mapeando para
  `NoxyDBServerError(status, texto)`. Hoje exige JSON, então os códigos novos
  virariam `NoxyDBConnectionError` genérico.
- Novo `NoxyDBClient.shutdown()`, útil apenas contra servidor iniciado com
  `--enable-shutdown`.
- **Nenhuma exceção nova.** Os códigos novos cabem no `.status` que
  `NoxyDBServerError` já carrega.
- Validação de documento, domínio JSON e `Database.close()` como close remoto
  lógico: tudo inalterado.

---

## 9. Testes

### 9.1 Migração

Os 19 arquivos `.nx` migram para `ref`. `http_transport_test.nx` vira
`api_test.nx` e passa a testar roteamento, mapeamento de operação e linha de
atividade — **não** framing, que agora é responsabilidade da stdlib e é testado
lá.

`tests/run_tests.ps1` troca `http_transport_test.nx` por `api_test.nx` na lista
`$serverTests`.

### 9.2 Testes de integração afetados

| teste | hoje | depois |
|---|---|---|
| `test_declared_request_over_one_mib_is_rejected` | 400 JSON | **413** `text/plain` |
| `test_fragmented_surplus_is_rejected_before_routing` | 400, roteamento não ocorre | **removido e substituído** |
| `test_fragmented_http_request_is_assembled` | monta o request fragmentado | inalterado no contrato, revalidar |
| `test_incomplete_client_does_not_block_other_clients` | 400 após timeout artesanal | **408** `text/plain` |

O teste de surplus **perde a premissa**, não muda de asserção: a stdlib descarta
os bytes excedentes em vez de rejeitá-los (§6.4). Ele é removido e substituído
por um que fixa o comportamento novo — um request completo seguido de bytes
pipelined é atendido normalmente, os bytes extras são descartados, e a conexão
fecha após uma resposta. Trocar uma asserção de rejeição por uma de descarte
sem dizer isso em voz alta esconderia uma regressão de rigor atrás de um teste
verde.

### 9.3 Testes novos

| Teste | Assere |
|---|---|
| Encerramento por rota | os `.db` em cache foram fisicamente fechados antes da saída |
| Encerramento por sinal | SIGTERM produz o mesmo estado final |
| Rota sem a flag | `/v1/shutdown` responde 404 sem `--enable-shutdown` |
| Regressão ler-antes-de-escrever | um laço de `exists`+`put` permanece linear |
| Replay com hex não-UTF-8 | devolve erro de replay em vez de derrubar o processo |

| Worker morto não pendura o cliente | um worker que falha produz encerramento com diagnóstico, não request travado |

O teste de regressão de 9.3 é o que impede alguém de reintroduzir uma assinatura
por valor sem perceber.

### 9.4 `defer` para limpeza garantida nos testes

Os 19 testes `.nx` criam um `.db`, exercitam e removem o arquivo na última
linha. Quando uma asserção falha antes disso, o arquivo **vaza** e contamina a
execução seguinte.

Isso não é hipotético: no diagnóstico inicial deste refactor,
`persistence_read_test.nx` leu o arquivo deixado pela execução anterior de
`persistence_write_test.nx` (a ordem alfabética coloca `read` antes de `write`)
e o resultado mudou entre duas rodadas idênticas. Falhas mascaradas por estado
residual foram parte do custo de diagnosticar isso.

Cada teste passa a registrar a limpeza logo após criar o arquivo:

```noxy
let path: string = "tests/document_isolation.db"
if io.exists(path) then io.remove(path) end
defer io.remove(path)
```

O `final-fixes-report.md` do projeto registrou essa lacuna na época como "the
strongest guaranteed cleanup available without Noxy exceptions or a `finally`
construct". O `defer` chegou no PR #18 e fecha a lacuna.

**Escopo deliberado:** `defer` entra **apenas nos testes**. No código de
produção os caminhos de abertura e fechamento não têm retorno antecipado entre
`io.open` e `io.close_result`, e a spec da linguagem diz que o resultado
ordinário de um `io.close_result(...)` deferido é descartado — o que conflita
diretamente com `close_database` precisar reportar falha de fechamento.

---

## 10. Versão e documentação

| Alvo | Mudança |
|---|---|
| `noxy.mod` | `noxy v0.1.0` → `noxy v0.4.0` |
| `CHANGELOG.md` | entrada 0.3.0 registrando o breaking de `ref` |
| `README.md` | usage com `ref`; parágrafos do semáforo, do surplus e de sinais removidos; contrato de status codes atualizado; `--enable-shutdown` documentado |
| `docs/noxydb-como-funciona.md` | seção 2 reescrita (isolamento → densidade, com os números de 2.2); seções 23–24 reescritas para o servidor novo |

---

## 11. Fora de escopo

Queries, JSON Path, updates parciais, índices, schemas, coleções, filtros,
compaction, TTL, transações, replicação e sharding permanecem fora, como já
estavam.

Também fora: alinhar `remove` com `PutResult` (§3.1); `defer` no código de
produção (§9.4); qualquer alteração no repositório da linguagem; `fsync` e
durabilidade a crash.

---

## 12. Riscos e o que não foi verificado

- **Nenhum dos 8 testes falhos foi corrigido ainda.** O design se apoia em
  spikes isolados que reproduzem os padrões, não na suíte real migrada.
- **Não medi o servidor novo sob carga concorrente.** O spike validou
  funcionamento, não throughput nem comportamento sob conexões simultâneas.
  A remoção do semáforo de rede precisa ser confirmada pelo teste de
  concorrência existente (`test_concurrent_clients_are_serialized_without_lost_documents`).
- **`docs/noxydb-como-funciona.md` tem 1.270 linhas.** Só as seções 2 e 23–24
  estão identificadas como afetadas; uma leitura completa durante a
  implementação pode revelar mais.
- **Ordem alfabética nos testes de persistência** faz `persistence_read` ler o
  arquivo deixado pela execução anterior de `persistence_write`. Isso mascarou
  falhas no diagnóstico inicial. O `defer` da §9.4 remove o vazamento, mas a
  dependência de ordem entre os dois arquivos é um acoplamento à parte e
  precisa ser tratada explicitamente na migração — `defer` sozinho não a
  resolve.
- **A supervisão não foi exercitada contra o servidor real.** O contrato de
  `spawn_task`/`task_await` está verificado (§2.4), mas com workers sintéticos.
  Falta confirmar o intervalo de poll da supervisora e que ela não compete com
  o handshake de encerramento da §7.2 — os dois observam o mesmo worker por
  caminhos diferentes.
