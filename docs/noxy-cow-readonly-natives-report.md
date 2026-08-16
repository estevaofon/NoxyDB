# Relatório: `has_key` e `keys` tornam a escrita seguinte quadrática (CoW v0.4.0)

**Repositório alvo:** `D:\OneDrive\Documentos\go_projects\noxy`
**Arquivo a corrigir:** `internal/vm/cow_natives.go`
**Descoberto em:** refactor do NoxyDB para Noxy v0.4.0, 2026-08-16
**Severidade:** performance — correção silenciosa, sem erro, sem crash

> ## ✅ RESOLVIDO — commit `9ada148` (PR #28), 2026-08-16
>
> `has_key` e `keys` entraram na allowlist. Verificado por re-execução da
> reprodução da seção 4 contra o binário corrigido:
>
> | N | só `put` | índice + `put` | `has_key` + `put` (antes) | `has_key` + `put` (depois) |
> |---:|---:|---:|---:|---:|
> | 1.250 | 3 ms | 4 ms | 290 ms | **3 ms** |
> | 2.500 | 5 ms | 9 ms | 1.276 ms | **7 ms** |
> | 5.000 | 12 ms | 16 ms | 5.807 ms | **22 ms** |
> | 10.000 | 32 ms | 41 ms | — | **35 ms** |
>
> 264x em N=5000, e o crescimento voltou a ser linear. A correção veio com
> três testes de regressão ancorados em `CloneCountValue`, incluindo o caso
> negativo que pina o default conservador para natives fora da lista.
>
> **Correção a uma afirmação deste relatório:** a medição de `keys` da seção 3
> estava contaminada. Ver a nota na seção 3.

---

## 1. Resumo

A allowlist `readonlyNatives` de `internal/vm/cow_natives.go` tem 8 entradas e
não inclui `has_key` nem `keys`. Como o default do CoW é marcar
conservadoramente, passar um `map` para qualquer um dos dois marca esse mapa
como *Shared*. A **próxima mutação** do mapa precisa unicizá-lo, e clona a
estrutura inteira.

O efeito é que o padrão ler-antes-de-escrever — o padrão normal de qualquer
banco de dados, cache ou índice — passa a custar um clone completo por escrita,
tornando quadrático um laço que deveria ser linear.

Auditei os corpos em Go dos dois: nenhum retém ou muta o argumento. Ambos
qualificam para a allowlist pelo critério que o próprio arquivo declara.

---

## 2. Ambiente

| | |
|---|---|
| Executável | `C:\Users\estev\go\bin\noxy.exe` — `Noxy v0.4.0` |
| Commit do repo | `6d7e051` (branch `main`, árvore limpa) |
| Plataforma | Windows 11 |

> Nota lateral: `D:\OneDrive\Documentos\go_projects\noxy\noxy.exe` ainda reporta
> `Noxy v0.3.0`. O binário em `~\go\bin` é o atual.

---

## 3. Sintoma medido

Mesmo laço, N=5000, variando apenas a leitura intercalada entre as escritas.
Os números abaixo são a saída do script da seção 4, exatamente como está lá:

| padrão do laço | tempo | escala |
|---|---:|---|
| só `put` (escrita pura) | 11 ms | linear |
| `put` + leitura por índice `payloads[key]` | 14 ms | linear |
| `put` + `has_key(payloads, key)` | **5.807 ms** | quadrática |

Crescimento dobrando N, para o caso `has_key`:

| N | só `put` | índice + `put` | `has_key` + `put` | fator |
|---:|---:|---:|---:|---:|
| 1.250 | 4 ms | 3 ms | 290 ms | — |
| 2.500 | 5 ms | 12 ms | 1.276 ms | 4,4x |
| 5.000 | 11 ms | 14 ms | 5.807 ms | 4,6x |

Dobrar N quase quintuplica o tempo: O(N²). As duas primeiras colunas continuam
lineares no mesmo intervalo.

Uma variante do laço trocando `has_key` por `keys(payloads)` mediu 7.863 ms em
N=5000. Essa medição veio de um script separado, não do da seção 4.

> **Correção (pós-fix).** Essa medição de `keys` **não era evidência limpa do
> bug** e não deveria ter entrado na mesma tabela que a de `has_key`. Chamar
> `keys()` dentro de um laço é O(N²) por construção, independente de CoW: cada
> chamada aloca um array com todas as chaves do mapa. Medido depois da
> correção, `keys()` em laço com **zero** escritas intercaladas custa 12.018 ms
> em N=5000 — ou seja, o custo é intrínseco, não vinha do clone.
>
> A evidência do `has_key` é que era limpa: `has_key` é O(1), então o
> comportamento quadrático só podia vir da unicização.
>
> Isso não enfraquece a entrada de `keys` na allowlist, que se sustenta pela
> auditoria da seção 6 (`Snapshot` + array novo, sem compartilhar estrutura) e
> ficou provada pelo teste de contador de clones que acompanhou a correção.
> O que estava errado era usar tempo de parede como prova nesse caso.

Há variação de alguns milissegundos entre execuções nas colunas lineares; ela é
irrelevante frente às três ordens de grandeza da coluna quadrática.

O contraste com a leitura por índice é o que localiza a causa. Indexar um
contêiner é caminho de bytecode e não marca nada — 14 ms. Passar o mesmo mapa
para um native fora da allowlist marca — 5.172 ms. A diferença entre os dois é
**apenas** a allowlist.

---

## 4. Reprodução mínima

Três arquivos, sem dependências.

`noxy.mod`:

```
module cowtrap

noxy v0.4.0
```

`mod/db.nx`:

```noxy
struct State
    payloads: map[string, string]
    n: int
end
struct Database
    state: State
end
func open_db() -> Database
    let p: map[string, string] = {}
    return Database(State(p, 0))
end
func put(db: ref Database, key: string, value: string) -> bool
    db.state.payloads[key] = value
    db.state.n = db.state.n + 1
    return true
end
```

`main.nx`:

```noxy
use mod.db as db
use time
use sys

let N: int = to_int(sys.argv()[2])

// 1. só put
let a: db.Database = db.open_db()
let t0: int = time.now_ms()
let i: int = 0
while i < N do
    let ok: bool = db.put(ref a, "key:" + to_str(i), "v")
    i = i + 1
end
let t1: int = time.now_ms()

// 2. leitura por índice + put
let b: db.Database = db.open_db()
let j: int = 0
while j < N do
    let v: string = b.state.payloads["key:" + to_str(j)]
    let ok: bool = db.put(ref b, "key:" + to_str(j), "v")
    j = j + 1
end
let t2: int = time.now_ms()

// 3. has_key + put
let c: db.Database = db.open_db()
let k: int = 0
while k < N do
    let e: bool = has_key(c.state.payloads, "key:" + to_str(k))
    let ok: bool = db.put(ref c, "key:" + to_str(k), "v")
    k = k + 1
end
let t3: int = time.now_ms()

print("N=" + to_str(N) + "  so_put=" + to_str(t1-t0) + "ms  index+put=" + to_str(t2-t1) + "ms  has_key+put=" + to_str(t3-t2) + "ms")
```

Rodar:

```powershell
noxy main.nx 1250
noxy main.nx 2500
noxy main.nx 5000
```

Trocar `has_key(c.state.payloads, ...)` por `keys(c.state.payloads)` reproduz o
mesmo comportamento, um pouco pior.

---

## 5. Causa raiz

`internal/vm/cow_natives.go`, íntegra:

```go
// readonlyNatives lista natives sem assinatura que comprovadamente não retêm
// nem mutam seus argumentos: seus args compostos não precisam de MarkShared.
// Só entra aqui native auditado — o default conservador é marcar.
var readonlyNatives = map[string]bool{
	"length":      true,
	"to_str":      true,
	"to_int":      true,
	"to_float":    true,
	"fmt":         true,
	"typeof":      true,
	"chan_recv":   true, // recebe o canal; o payload foi marcado no send
	"test_report": true, // harness de teste: apenas observa
}
```

São 8 entradas, e nenhuma delas é uma operação de coleção. O default
conservador está correto como default; o problema é que a allowlist nunca
cresceu para cobrir os natives de leitura de coleção, que são justamente os que
aparecem no caminho quente de estruturas de dados reais.

O CHANGELOG do v0.4.0 já descreve o mecanismo corretamente:

> Natives sem assinatura marcam os args conservadoramente; uma allowlist
> auditada de natives só-leitura (`internal/vm/cow_natives.go`) evita o custo
> onde é provado desnecessário.

Este relatório é sobre um caso onde é provado desnecessário e o custo não foi
evitado.

---

## 6. Auditoria dos dois natives

Critério declarado pelo arquivo: *não retém nem muta seus argumentos*.

### `has_key` — `internal/vm/builtins_collections.go:228`

```go
vm.DefineNative("has_key", func(args []value.Value) value.Value {
	if len(args) != 2 {
		return value.NewBool(false)
	}
	mapVal := args[0]
	keyVal := args[1]
	if mapVal.Type == value.VAL_OBJ {
		if mapObj, ok := mapVal.Obj.(*value.ObjMap); ok {
			var key interface{}
			// ... normalização da chave (int64 ou string) ...
			_, ok := mapObj.Get(key)
			return value.NewBool(ok)
		}
	}
	return value.NewBool(false)
})
```

Faz uma única chamada `mapObj.Get(key)` e devolve um `bool` novo. Não escreve
no mapa, não guarda o ponteiro, não devolve nada derivado dele.
**Qualifica.**

### `keys` — `internal/vm/builtins_collections.go:34`

```go
vm.DefineNative("keys", func(args []value.Value) value.Value {
	// ...
	if m, ok := mapVal.Obj.(*value.ObjMap); ok {
		values := m.Snapshot()
		keys := make([]value.Value, 0, len(values))
		for k := range values {
			if kInt, ok := k.(int64); ok {
				keys = append(keys, value.NewInt(kInt))
			} else if kStr, ok := k.(string); ok {
				keys = append(keys, value.NewString(kStr))
			}
		}
		return value.NewArray(keys)
	}
	// ...
})
```

Lê por `Snapshot()` e monta um slice **novo**, com valores int/string
recém-criados. O `ObjArray` devolvido não compartilha estrutura com o
`ObjMap`. Não muta, não retém. **Qualifica.**

---

## 7. Correção proposta

```go
var readonlyNatives = map[string]bool{
	"length":      true,
	"to_str":      true,
	"to_int":      true,
	"to_float":    true,
	"fmt":         true,
	"typeof":      true,
	"chan_recv":   true,
	"test_report": true,
	"has_key":     true, // só consulta; devolve bool
	"keys":        true, // Snapshot + array novo; não compartilha estrutura
}
```

---

## 8. Teste de regressão sugerido

`vm.CloneCountValue()` já existe (`internal/vm/cow.go:13`) e já é usado em
`internal/vm/cow_test.go` e `internal/vm/value_semantics_test.go`. O teste
natural é o mesmo formato de `value_semantics_test.go:237`, que assere 0
clones:

> um laço que alterna `has_key(m, k)` e `m[k] = v` sobre o mesmo mapa deve
> terminar com contador de clones 0.

Sem a correção esse contador é N. Isso ancora a regressão no invariante e não
no relógio, evitando um teste sensível a máquina.

Vale também um caso para `keys`, e um caso negativo que garanta que a allowlist
**não** cresceu para natives mutantes: `delete` e `append` precisam continuar
clonando.

---

## 9. Candidatos adicionais (NÃO auditados)

Não li o corpo em Go de nenhum destes. Entram como fila de auditoria, não como
recomendação:

| native | por que é candidato | risco a checar |
|---|---|---|
| `json_dumps`, `json_dumps_result` | lê um composto e produz string | se guarda referência em algum cache |
| `contains` | consulta de array | — |
| `slice` | pode copiar ou compartilhar backing store | **compartilhamento de estrutura** |
| `print`, `iprint` | só formatam | — |
| `net_send` | lê `bytes` | se retém o buffer |

Fora da lista por serem mutantes ou retentores, e que **não** devem entrar:
`append`, `pop`, `delete`, `json_loads` (escreve no mapa de saída), `chan_send`
(entrega o valor adiante), `spawn`/`spawn_task`.

`json_dumps` é o de maior impacto prático depois dos dois principais: serializar
um documento antes de gravá-lo é exatamente um padrão ler-depois-escrever.

---

## 10. Impacto medido no NoxyDB

O NoxyDB (`D:\OneDrive\Documentos\noxy_projects\noxydb`) existe, segundo o
próprio README, como *"a real-world systems programming workload for Noxy,
exercising and helping drive the evolution of the language"*. Ele bate neste
problema em quatro pontos, todos no caminho quente:

| local | padrão |
|---|---|
| `noxydb/noxydb.nx:100` — `get` | `has_key(db.state.payloads, key)` antes de todo `put` seguinte |
| `noxydb/noxydb.nx:113` — `remove` | `has_key` seguido de `delete` no mesmo mapa |
| `noxydb/noxydb.nx:124` — `exists` | `has_key` sobre o mapa de estado |
| `noxydb/storage.nx:76-78` — `replay` | `has_key` + `delete` por registro `D`, dentro do laço de replay |

O caso do `replay` é o mais grave: **abrir** um banco com muitas remoções é
quadrático no tamanho do log, antes de qualquer operação do usuário.

`noxydb/noxydb.nx:33` (`payloads_are_valid`) e
`server/database_worker.nx:77` (`close_all_databases`) usam `keys()` sobre o
estado.

---

## 11. O que eu NÃO verifiquei

Explicitamente fora do que foi medido:

- **Não apliquei a correção nem rebuildei.** Que adicionar as duas entradas
  elimina o comportamento quadrático é inferência a partir do sintoma medido e
  da leitura da causa. Precisa ser confirmado com `go build` e re-medição.
- **Não medi contagem de clones de dentro do `.nx`.** `CloneCountValue` é
  interno ao Go e não tem native exposto, então a evidência aqui é tempo de
  parede, não o contador. A correlação com o clone vem da leitura do código.
- **Não auditei os candidatos da seção 9.**
- **Não investiguei se `MarkShared` poderia ser mais preciso** em vez de
  depender de uma allowlist por nome. A allowlist é o mecanismo que existe;
  se o desenho certo é outro, este relatório não responde.
