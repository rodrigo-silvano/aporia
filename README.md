# APORIA

<p align="center">
  <strong>Cognitive Architecture, Scientific Safety & Runtime Governance for Autonomous Agents</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9+-blue.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/dependencies-zero%20external-brightgreen.svg" alt="Zero External Dependencies">
  <img src="https://img.shields.io/badge/tests-162%20passed-success.svg" alt="162 Tests Passed">
  <img src="https://img.shields.io/badge/architecture-harness--agnostic-orange.svg" alt="Harness Agnostic">
  <img src="https://img.shields.io/badge/license-MIT-lightgrey.svg" alt="License">
</p>

---

## O que é o APORIA?

O **APORIA** é uma arquitetura cognitiva de **governação em tempo de execução e segurança científica** desenhada para agentes de inteligência artificial autónomos.

Quando agentes de IA operam no mundo real — invocando ferramentas, modificando bases de dados, enviando e-mails ou executando código — precisam de salvaguardas rigorosas. O APORIA atua como um **plano de controlo independente**, garantindo que qualquer ação de um agente é auditada, avaliada quanto ao risco e sujeita a interruptores de emergência (*kill switches*) que **falham sempre para o estado seguro (*fail-closed*)**.

Construído com uma filosofia de **zero dependências externas** (utiliza exclusivamente a biblioteca padrão de Python 3.9+), o APORIA é **100% harness-agnostic**: funciona de forma transparente com qualquer modelo de linguagem e qualquer framework de agentes (Antigravity, LangChain, AutoGen, CrewAI ou gateways HTTP proprietários).

---

## Pilares Fundamentais

```
                 ┌───────────────────────────────────────┐
                 │       Agente Autónomo / LLM           │
                 │  (Antigravity / LangChain / CrewAI)   │
                 └──────────────────┬────────────────────┘
                                    │ (JWT HS256)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                               APORIA                                   │
│                                                                        │
│   ┌─────────────────────┐    ┌─────────────────────────────────────┐   │
│   │   Control Plane     │    │        Effect Compiler              │   │
│   │  • Kill Switches    │    │  • Avaliação de Risco (0.05 - 0.95) │   │
│   │  • Fail-Closed      │    │  • Portões de Aprovação             │   │
│   │  • Resolução Modos  │    │  • Compromissos Criptográficos      │   │
│   └──────────┬──────────┘    └──────────────────┬──────────────────┘   │
│              │                                  │                      │
│              ▼                                  ▼                      │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │                      Causal Event Fabric                       │   │
│   │  • Relógio Lógico Híbrido (HLC)   • Ingestão Idempotente       │   │
│   │  • DAG Causal & Linhagem          • Hashing Canónico SHA-256   │   │
│   └──────────────────────────────┬─────────────────────────────────┘   │
│                                  │                                     │
│                                  ▼                                     │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │               Autobiografia & Continuidade                     │   │
│   │  • Cadeia Autobiográfica Verificada   • Snapshots de Identidade│   │
│   └────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```

### 1. Plano de Controlo Independente (*Fail-Closed Safety*)
Disponibiliza 6 interruptores de emergência independentes (`global`, `tenant`, `runtime_influence`, `memory_writes`, `ontology`, `hirt_advisory`). Se ocorrer qualquer falha na base de dados ou na rede, o sistema desativa automaticamente operações de risco por omissão.

### 2. Compilação Determinística de Efeitos
Antes de executar qualquer ferramenta, a ação é compilada com um cálculo de risco determinístico (de `0.05` para leituras seguras até `0.95` para efeitos externos irreversíveis). Ações críticas exigem aprovação explícita através de compromissos criptográficos imutáveis.

### 3. *Causal Event Fabric* com Relógio Híbrido (HLC)
Registo de eventos do agente num grafo acíclico direcionado (DAG). Através de um *Hybrid Logical Clock*, garante ordenação causal estrita, deteção de concorrência e eliminação de duplicados com *hashing* determinístico SHA-256.

### 4. Continuidade de Identidade e Autobiografia
Regista a trajetória evolutiva do agente em cadeias autobiográficas com assinaturas criptográficas encadeadas, permitindo exportar evidências formais de que o comportamento do agente se manteve alinhado com as políticas estabelecidas.

### 5. Zero Dependências Externas
Zero bibliotecas `pip`. Toda a criptografia (HMAC-SHA256, tokens JWT, canonicalização JSON, framing binário) é implementada nativamente em Python standard library.

---

## Como Começar

### Pré-requisitos
- Python 3.9 ou superior (sem necessidade de `pip install` de pacotes externos)

### Configuração
Copia o ficheiro de exemplo de variáveis de ambiente:
```bash
cp .env.example .env
```

### Inicialização Rápida em Python

```python
from aporia.client import AporiaClient
from aporia.infrastructure.db import get_connection
from aporia.infrastructure.schema import SCHEMA_SQL

# 1. Conectar à base de dados (em memória ou SQLite local)
conn = get_connection()
conn.executescript(SCHEMA_SQL)

# 2. Inicializar o cliente APORIA
client = AporiaClient(pdo=conn, secret="o-teu-bridge-secret")
assert client.is_healthy()

# 3. Consultar o plano de controlo
snapshot = client.controls.snapshot(tenant_id=1)
print(snapshot)
# {'global': False, 'tenant': False, 'runtime_influence': False, ...}
```

### Autenticação Universal com Qualquer Framework

Qualquer agente pode comunicar com o APORIA através de tokens JWT padrão:

```python
from aporia.crypto import jwt_encode
import time

token = jwt_encode(
    {
        "iss": "meu-framework-de-agentes",  # Ex: "langchain", "crewai", "antigravity"
        "aud": "aporia-runtime",
        "tenant_id": 1,
        "agent_session_id": "sessao-001",
        "exp": int(time.time()) + 3600,
    },
    key="o-teu-bridge-secret",
)
```

---

## Ferramentas CLI Disponíveis

O projeto inclui utilitários de linha de comandos prontos a usar:

| Script | Descrição | Exemplo de Uso |
| :--- | :--- | :--- |
| **`aporia_health.py`** | Verificação de integridade e snapshot do plano de controlo | `python3 .agents/plugins/aporia/scripts/aporia_health.py` |
| **`aporia_controls.py`** | Consulta de kill switches e resolução de modo por tenant | `python3 .agents/plugins/aporia/scripts/aporia_controls.py --tenant-id 1` |
| **`aporia_ingest.py`** | Ingestão causal de eventos com hashing SHA-256 e relógio HLC | `python3 .agents/plugins/aporia/scripts/aporia_ingest.py --tenant-id 1 --event-kind turn.started` |
| **`aporia_export.py`** | Exportação verificada da cadeia autobiográfica | `python3 .agents/plugins/aporia/scripts/aporia_export.py --tenant-id 1` |

---

## Integração como Plugin Antigravity

O APORIA está empacotado como um plugin nativo para agentes **Antigravity** em `.agents/plugins/aporia/`, dispondo de 3 *skills* especializadas com *Progressive Disclosure*:

1. **`aporia`**: Skill router de configuração rápida, verificação de saúde e visão geral.
2. **`aporia-governance`**: Invocação de kill switches, consulta de modos operacionais (`disabled`, `advisory`, `guarded_reversible`) e compilação de efeitos.
3. **`aporia-observe`**: Ingestão de telemetria, análise de DAG causal e auditoria comportamental.

---

## Estrutura do Repositório

```text
aporia/
├── aporia/                      # Pacote principal de governação cognitiva
│   ├── api/                     # Adaptadores de endpoints HTTP
│   ├── application/             # Resolução de modos de runtime e exportação
│   ├── crypto.py                # Primitivas criptográficas puras (SHA-256, HMAC, JWT)
│   ├── harness/                 # Adaptadores plugáveis de frameworks de agentes
│   ├── infrastructure/          # Control plane, event fabric, effect compilers, SQLite
│   ├── client.py                # Interface de alto nível AporiaClient
│   └── config.py                # Gestão de variáveis de ambiente
├── .agents/plugins/aporia/      # Plugin Antigravity (Skills, Scripts, Regras)
├── tests/                       # Suíte completa de 162 testes unitários
├── experiments/                 # Protocolos empíricos e modelos formais
├── platform/                    # Migrações de base de dados e pipeline de qualificação
├── AGENTS.md                    # Instruções obrigatórias para agentes autónomos
├── CLAUDE.md                    # Diretrizes operacionais para Claude Code
└── README.md                    # Apresentação do projeto
```

---

## Testes e Verificação

A integridade do sistema é validada por **162 testes automatizados** com cobertura profunda de invariantes matemáticos e criptográficos:

```bash
python3 -m unittest discover tests -p "test_*.py"
```

```text
Ran 162 tests in 6.475s
OK (skipped=2)
```

---

## Licença

Distribuído sob a licença MIT. Consulta `LICENSE` para mais detalhes.
