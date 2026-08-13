# Como o NoxyDB funciona

Este documento explica, em profundidade, como o NoxyDB funciona internamente, com foco em:

- arquitetura geral;
- modelo de dados;
- estado em memória;
- fluxo de `put`, `get`, `remove` e `open_database`;
- funcionamento do append-only log;
- replay;
- isolamento dos documentos;
- papel do servidor;
- responsabilidades de cada camada.

---

## 1. Visão geral do banco

O NoxyDB é, no núcleo, um banco **key-value de documentos**:

- a **chave** é uma `string`;
- o **valor** é um **documento JSON**, representado na API como `map[string, any]`;
- internamente, o banco **não guarda o documento como objeto vivo**;
- ele guarda o documento como **JSON serializado em string**;
- a persistência é feita por meio de um **append-only log** em disco.

A arquitetura principal pode ser visualizada assim:

```text
Aplicação
   │
   ▼
put/get/remove/exists
   │
   ▼
NoxyDB API
   │
   ├── document.nx
   │     ├── serializa map[string, any] -> JSON string
   │     └── desserializa JSON string -> map[string, any]
   │
   ▼
DatabaseState.payloads
(map[string, string])
   │
   │   chave -> payload JSON serializado
   │
   ▼
storage.nx
   ├── append_put
   ├── append_remove
   └── replay
   │
   ▼
arquivo .db
(append-only log)
```

A ideia central é manter bem separadas duas responsabilidades:

```text
Modelo de documento
        ↓
JSON serializado
        ↓
Storage engine
```

A storage engine não precisa entender objetos, arrays, números ou booleanos. Para ela, o valor é apenas um payload opaco.

---

## 2. Por que o estado em memória guarda string e não objeto?

Essa é uma das decisões arquiteturais mais importantes do NoxyDB.

Em vez de guardar isto em memória:

```text
"user:1" -> {"name": "Estevao", "profile": {...}}
```

o banco guarda:

```text
"user:1" -> "{\"name\":\"Estevao\",\"profile\":{...}}"
```

Ou seja: o **JSON serializado**.

### Motivo

Maps, arrays e structs da Noxy possuem semântica de shallow copy. Se o banco mantivesse documentos decodificados como estado autoritativo, uma referência compartilhada para uma estrutura aninhada poderia permitir que o chamador alterasse o estado do banco sem passar por `put()`.

Isso seria muito perigoso, porque uma alteração como:

```text
doc = get("user:1")
doc["name"] = "Mudou"
```

poderia potencialmente modificar memória sem gerar nenhuma escrita no log.

O NoxyDB evita isso serializando os documentos.

### Fluxo do isolamento

```text
PUT
entrada do usuário: map[string, any]
        │
        ▼
serialize()
        │
        ▼
payload JSON string
        │
        ├── vai para o log
        └── vai para o map interno
```

No `get()`:

```text
GET
payload JSON string
        │
        ▼
deserialize()
        │
        ▼
novo map[string, any]
        │
        ▼
entregue ao usuário
```

Assim:

- modificar o documento original depois de `put()` não altera o banco;
- modificar o documento retornado por `get()` não altera o banco;
- cada leitura recebe uma nova estrutura;
- o estado autoritativo continua exatamente igual ao payload persistido.

---

## 3. O `DatabaseState`

O estado principal do banco pode ser representado conceitualmente assim:

```text
DatabaseState
├── payloads: map[string, string]
├── file_fd: int
├── path: string
├── open: bool
└── error: string
```

Cada campo tem uma responsabilidade:

### `payloads`

É o estado atual do banco em memória.

Exemplo:

```text
"user:1" -> "{\"name\":\"Estevao\",\"age\":30}"
"user:2" -> "{\"name\":\"Maria\"}"
```

Ele funciona como índice principal do banco.

### `file_fd`

Identifica o arquivo de log aberto.

### `path`

Caminho do arquivo `.db`.

### `open`

Indica se o banco está utilizável.

### `error`

Armazena um erro fatal do banco.

---

## 4. Estados possíveis

O NoxyDB pode ser entendido como possuindo três estados:

```text
OPEN
open = true
error = ""
```

```text
CLOSED
open = false
error = ""
```

```text
FAILED
open = false
error != ""
```

Portanto:

```text
is_open(db)
database_error(db)
```

permitem distinguir:

```text
banco funcionando
banco fechado normalmente
banco fechado por erro
```

---

# 5. Como funciona `open_database`

Ao abrir um banco, o NoxyDB precisa reconstruir o estado que existia antes do encerramento do processo.

O fluxo é:

```text
open_database(path)
   │
   ├── se o arquivo existe:
   │      ├── replay(path)
   │      ├── se replay falhar -> banco FAILED
   │      ├── valida cada payload como documento JSON
   │      └── se algum payload for inválido -> banco FAILED
   │
   ├── abre o arquivo em modo append
   ├── se falhar -> banco FAILED
   └── retorna banco OPEN
```

Visualmente:

```text
arquivo .db
   │
   ▼
storage.replay()
   │
   ▼
map[string, string]
(payloads reconstruídos)
   │
   ▼
payloads_are_valid()
   │
   ├── algum JSON inválido -> FAILED
   └── tudo válido
   │
   ▼
io.open(path, "a")
   │
   ├── falha -> FAILED
   └── sucesso -> OPEN
```

Um detalhe importante é a ordem:

```text
replay
   ↓
validação
   ↓
abertura para append
```

O arquivo não é aberto para novas escritas enquanto o estado anterior não tiver sido validado completamente.

---

# 6. Como funciona o `put`

O `put()` recebe:

```text
key: string
value: map[string, any]
```

O fluxo é:

```text
put(db, key, document)
   │
   ├── banco está aberto?
   │
   ├── serialize(document)
   │
   ├── append_put(file, key, payload)
   │
   └── payloads[key] = payload
```

Mais detalhadamente:

```text
put(db, key, doc)
   │
   ├── se db não está open -> erro
   │
   ├── document.serialize(doc)
   │      ├── falha
   │      │      -> documento inválido
   │      │      -> banco continua saudável
   │      └── sucesso
   │             -> payload JSON
   │
   ├── storage.append_put(file, key, payload)
   │      ├── falha
   │      │      -> banco entra em FAILED
   │      └── sucesso
   │
   └── db.state.payloads[key] = payload
```

A regra essencial é:

```text
1. escreve no log
2. atualiza memória
```

e nunca:

```text
1. atualiza memória
2. tenta escrever no log
```

Isso garante que o estado em memória não indique uma alteração que não foi persistida.

---

# 7. Como funciona o `get`

O `get()` **não consulta o arquivo**.

Depois que o banco está aberto, o estado já foi reconstruído em memória.

Fluxo:

```text
get(db, key)
   │
   ├── banco está aberto?
   ├── chave existe?
   │
   ├── pega payload do map
   │
   ├── deserialize(payload)
   │
   └── retorna novo documento
```

Exemplo:

```text
GET "user:1"
   │
   ▼
payloads["user:1"]
   │
   ▼
"{\"name\":\"Estevao\",\"age\":30}"
   │
   ▼
deserialize()
   │
   ▼
{
    "name": "Estevao",
    "age": 30
}
```

Toda leitura cria uma nova estrutura.

Isso garante isolamento entre o estado interno do banco e o objeto manipulado pelo chamador.

---

# 8. Como funciona o `remove`

`remove()` utiliza um **tombstone**.

Um tombstone é um registro no log informando que uma chave deixou de existir.

Fluxo:

```text
remove(db, key)
   │
   ├── banco está aberto?
   ├── chave existe?
   │
   ├── append_remove(file, key)
   │
   └── delete(payloads, key)
```

Assim como no `put`, a ordem é:

```text
1. persiste o tombstone
2. remove da memória
```

Se a chave não existir, a operação é um no-op.

---

# 9. O append-only log

O sistema de persistência do NoxyDB é baseado em um **append-only log**.

Append-only significa:

> registros antigos não são modificados ou sobrescritos; cada nova operação é acrescentada no final do arquivo.

O arquivo funciona como uma sequência de eventos.

---

## 10. Formato do log

O formato físico é:

```text
P<TAB><key_hex><TAB><payload_hex>\n
D<TAB><key_hex>\n
```

Onde:

```text
P = PUT
D = DELETE / REMOVE
```

A chave e o payload são convertidos para hexadecimal.

Exemplo conceitual:

```text
P    757365723a31    7b226e616d65223a224573746576616f227d
D    757365723a31
```

---

# 11. Por que hexadecimal?

Uma string JSON pode conter:

- tabs;
- quebras de linha;
- aspas;
- espaços;
- Unicode;
- caracteres especiais.

Se o log armazenasse o JSON cru usando tabs e quebras de linha como delimitadores, seria necessário implementar uma camada de escaping.

Ao converter chave e payload para hexadecimal:

```text
conteúdo arbitrário
        ↓
hex
        ↓
apenas caracteres seguros
```

o envelope do log permanece extremamente simples:

```text
operação<TAB>campo<TAB>campo<NEWLINE>
```

A storage engine não precisa conhecer a gramática JSON.

---

# 12. Exemplo completo do log

Imagine estas operações:

```text
put("user:1", {"name":"Estevao","age":30})

put("user:2", {"name":"Maria"})

put("user:1", {"name":"Estevao","age":31})

remove("user:2")
```

Depois da primeira operação:

```text
user:1 -> {"name":"Estevao","age":30}
```

Depois da segunda:

```text
user:1 -> {"name":"Estevao","age":30}
user:2 -> {"name":"Maria"}
```

Depois da terceira:

```text
user:1 -> {"name":"Estevao","age":31}
user:2 -> {"name":"Maria"}
```

Depois da quarta:

```text
user:1 -> {"name":"Estevao","age":31}
```

Porém o arquivo contém o histórico completo:

```text
P    <hex("user:1")>    <hex('{"name":"Estevao","age":30}')>
P    <hex("user:2")>    <hex('{"name":"Maria"}')>
P    <hex("user:1")>    <hex('{"name":"Estevao","age":31}')>
D    <hex("user:2")>
```

O arquivo representa **eventos**.

O map em memória representa **estado atual**.

Essa diferença é fundamental.

---

# 13. Event log versus estado atual

Podemos pensar assim:

```text
                   LOG
                    │
                    │ histórico
                    ▼
        ┌──────────────────────┐
        │ PUT user:1 age=30    │
        │ PUT user:2 Maria     │
        │ PUT user:1 age=31    │
        │ DELETE user:2        │
        └──────────────────────┘
                    │
                  replay
                    ▼
        ┌──────────────────────┐
        │ ESTADO ATUAL         │
        │                      │
        │ user:1 -> age=31     │
        └──────────────────────┘
```

O log responde:

> O que aconteceu?

O map responde:

> Qual é o estado agora?

---

# 14. Como funciona o replay

Quando o banco abre, ele começa com:

```text
payloads = {}
```

e percorre cada registro do arquivo na ordem.

As regras são simples:

```text
P key payload
→ payloads[key] = payload
```

```text
D key
→ delete(payloads, key)
```

---

## 15. Replay passo a passo

Considere:

```text
P user:1 {...age:30}
P user:2 {...Maria}
P user:1 {...age:31}
D user:2
```

Inicialmente:

```text
{}
```

### Linha 1

```text
P user:1 {...age:30}
```

Estado:

```text
{
    user:1 -> {...age:30}
}
```

### Linha 2

```text
P user:2 {...Maria}
```

Estado:

```text
{
    user:1 -> {...age:30},
    user:2 -> {...Maria}
}
```

### Linha 3

```text
P user:1 {...age:31}
```

A chave já existe.

O valor mais recente vence:

```text
{
    user:1 -> {...age:31},
    user:2 -> {...Maria}
}
```

### Linha 4

```text
D user:2
```

Estado final:

```text
{
    user:1 -> {...age:31}
}
```

O banco está reconstruído.

---

# 16. Validação do replay

O replay é estrito.

Ele valida:

- se todo registro termina corretamente;
- se a operação é conhecida;
- se a quantidade de campos está correta;
- se o hexadecimal é válido;
- se a leitura corresponde ao tamanho esperado do arquivo.

Depois do replay físico, a camada do NoxyDB ainda valida:

```text
payload -> JSON object válido?
```

Portanto existe uma separação importante:

```text
storage.nx
    ↓
"o registro físico é válido?"
```

```text
document.nx / noxydb.nx
    ↓
"o payload representa um documento válido?"
```

Essa separação mantém a storage engine independente do modelo de dados.

---

# 17. O que acontece com um arquivo corrompido?

O NoxyDB não tenta adivinhar ou reparar silenciosamente.

Fluxo:

```text
arquivo .db
   │
   ▼
replay()
   │
   ├── registro inválido -> FAILED
   ├── hexadecimal inválido -> FAILED
   ├── linha truncada -> FAILED
   └── válido
   │
   ▼
validação dos payloads
   │
   ├── documento inválido -> FAILED
   └── todos válidos
   │
   ▼
abre para append
```

O banco somente começa a aceitar novas operações depois que a reconstrução foi concluída corretamente.

---

# 18. Por que o append-only log cresce indefinidamente?

Imagine:

```text
put("x", {"version":1})
put("x", {"version":2})
put("x", {"version":3})
put("x", {"version":4})
```

O estado atual é apenas:

```text
x -> {"version":4}
```

Mas o arquivo continua contendo:

```text
P x {"version":1}
P x {"version":2}
P x {"version":3}
P x {"version":4}
```

Isso acontece porque o NoxyDB não altera registros antigos.

O log guarda histórico.

---

# 19. Por que compaction é uma evolução natural?

Com o tempo:

```text
histórico cresce
       ↓
arquivo cresce
       ↓
replay demora mais
```

Mesmo que o estado atual seja pequeno.

A solução natural é **compaction**.

Conceitualmente:

```text
LOG ANTIGO

P user:1 version=1
P user:1 version=2
P user:2 Maria
D user:2
P user:1 version=3
```

Estado final:

```text
user:1 -> version=3
```

Uma compaction poderia produzir:

```text
NOVO LOG

P user:1 version=3
```

Assim:

```text
histórico desnecessário
        ↓
removido
        ↓
novo log representa apenas estado necessário
```

---

# 20. Responsabilidades das camadas

## Camada 1 — API de documentos

Responsável por:

```text
put
get
remove
exists
open_database
close_database
```

Ela trabalha com:

```text
string -> map[string, any]
```

---

## Camada 2 — `document.nx`

Responsável por:

```text
map[string, any]
        ↓ serialize
JSON string
```

e:

```text
JSON string
        ↓ deserialize
map[string, any]
```

Todo conhecimento sobre JSON fica nessa camada.

---

## Camada 3 — estado em memória

Responsável por:

```text
payloads: map[string, string]
```

Ele representa:

```text
key -> JSON serializado atual
```

e permite que `get()` e `exists()` sejam atendidos sem percorrer o arquivo.

---

## Camada 4 — `storage.nx`

Responsável por:

```text
append_put
append_remove
replay
```

Essa camada conhece:

```text
key
payload
P
D
hexadecimal
arquivo
```

Mas não conhece:

```text
JSON object
array
bool
number
null
```

---

# 21. Arquitetura resumida

```text
                     NoxyDB

         ┌───────────────────────────────┐
         │         API pública           │
         │ string -> map[string, any]    │
         └──────────────┬────────────────┘
                        │
                 put/get/remove
                        │
         ┌──────────────▼────────────────┐
         │        document.nx            │
         │ serialize / deserialize JSON  │
         └──────────────┬────────────────┘
                        │
         ┌──────────────▼────────────────┐
         │      DatabaseState            │
         │ payloads: map[string,string]  │
         │ open / error / file_fd        │
         └──────────────┬────────────────┘
                        │
         ┌──────────────▼────────────────┐
         │        storage.nx             │
         │ append_put / append_remove    │
         │ replay                        │
         └──────────────┬────────────────┘
                        │
         ┌──────────────▼────────────────┐
         │     append-only log file      │
         │ P <key_hex> <payload_hex>     │
         │ D <key_hex>                   │
         └───────────────────────────────┘
```

---

# 22. Princípios operacionais fundamentais

## PUT

```text
documento
   ↓
serialize
   ↓
grava log
   ↓
atualiza memória
```

## REMOVE

```text
chave
   ↓
grava tombstone
   ↓
remove da memória
```

## GET

```text
consulta map em memória
   ↓
obtém JSON serializado
   ↓
deserialize
   ↓
retorna documento novo
```

## OPEN

```text
lê arquivo
   ↓
replay
   ↓
reconstrói payloads
   ↓
valida documentos
   ↓
abre log para append
```

---

# 23. O servidor por cima do NoxyDB

O servidor não faz parte da storage engine.

Ele é uma camada acima do núcleo.

Arquitetura simplificada:

```text
Cliente Python / HTTP
          │
          ▼
     HTTP transport
          │
          ▼
       protocol
          │
          ▼
    database_worker
          │
          ▼
      NoxyDB API
          │
          ▼
    storage engine
```

O worker mantém algo conceitualmente como:

```text
databases: map[string, Database]
```

Por exemplo:

```text
"usuarios" -> Database(data/usuarios.db)
"cache"    -> Database(data/cache.db)
"sessoes"  -> Database(data/sessoes.db)
```

---

# 24. Fluxo de uma operação via servidor

Por exemplo:

```text
client.put(
    "user:1",
    {"name":"Estevao"}
)
```

pode percorrer:

```text
Python client
      │
      ▼
HTTP request
      │
      ▼
http_transport
      │
      ▼
protocol
      │
      ▼
database_worker
      │
      ▼
noxydb.put()
      │
      ▼
serialize JSON
      │
      ▼
append-only log
      │
      ▼
atualiza estado em memória
      │
      ▼
HTTP response
      │
      ▼
Python client
```

O servidor é, portanto, um **adaptador remoto** para a mesma API do banco.

---

# 25. Banco versus storage engine

Dentro do projeto existe uma distinção útil.

A **storage engine** resolve:

```text
como persistir?
como escrever?
como fazer replay?
como representar tombstones?
como reconstruir o estado?
```

O **NoxyDB como banco** resolve:

```text
qual é o modelo de dados?
o que é um documento?
como funciona put/get/remove?
como validar JSON?
como representar erro?
```

E o **servidor** resolve:

```text
como acessar isso remotamente?
como multiplexar bancos?
como transformar HTTP em operações do banco?
```

---

# 26. Uma única imagem mental para guardar

A forma mais simples de pensar no NoxyDB é:

```text
                    APLICAÇÃO
                        │
                        ▼
                chave -> documento
                        │
                        ▼
                    NoxyDB
                        │
                  serialize JSON
                        │
                        ▼
              chave -> JSON string
                        │
             ┌──────────┴──────────┐
             ▼                     ▼
       estado em memória        append log
             │                     │
             │                     │
          GET rápido          persistência
             │                     │
             └──────────┬──────────┘
                        │
                        ▼
                    replay
                        │
                        ▼
             estado reconstruído
```

---

# 27. Resumo final

O NoxyDB funciona usando duas representações diferentes para o mesmo dado:

### Representação externa

```text
string -> document
```

Exemplo:

```text
"user:1" ->
{
    "name": "Estevao",
    "age": 30
}
```

### Representação interna

```text
string -> JSON serializado
```

Exemplo:

```text
"user:1" ->
"{\"name\":\"Estevao\",\"age\":30}"
```

O estado atual fica em memória:

```text
map[string, string]
```

As mudanças são persistidas em um append-only log:

```text
P <key_hex> <payload_hex>
D <key_hex>
```

Ao reiniciar:

```text
log
 ↓
replay
 ↓
map reconstruído
 ↓
validação dos documentos
 ↓
banco pronto para novas operações
```

O princípio mais importante da arquitetura é:

> **O log é a fonte persistente do histórico; o map em memória representa o estado atual.**

E o princípio mais importante para isolamento é:

> **O banco nunca expõe seu estado interno mutável: documentos são serializados ao entrar e reconstruídos ao sair.**
