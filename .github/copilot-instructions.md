## DQC-QKD — instruções rápidas para agentes AI

Este repositório simula uma arquitetura distribuída de computação quântica com QKD. Use estas notas para atuar de forma produtiva e segura no código.

- Arquitetura (visão rápida):
  - `orchestrator/` (serviço central) — `orchestrator/app.py` recebe jobs em `/run`, solicita chaves ao QKDN (porta 8005) e despacha slices para QNodes.
  - `qnode/` (nós QPU) — `qnode/app.py` expõe `/execute_slice`, `/send_feedforward/{node}`, `/receive_secure_message`. Guarda chaves em `NODE_KEYS` (memória).
  - `sequence/` — simula criação de pares EPR (porta 8004). Envia métricas ao `collector`.
  - `qkdn/` — stub ETSI QKD (porta 8005). Retorna pares `{key_id, key_data_hex}`.
  - `collector/` — `collector/app.py` recebe métricas (`/submit_metric`, `/metrics`) e mantém `METRIC_STORE` em memória.

- Padrões e convenções do projeto:
  - Serviços são FastAPI + uvicorn. Cada componente pode ser executado com `python app.py` (veja `if __name__ == "__main__"` em cada `app.py`) ou via `uvicorn`.
  - Portas esperadas (testes e scripts):
    - Orchestrator: 8000
    - QNode (alice/bob): 8001 / 8002 (padrão; `qnode/app.py` aceita argumento de porta)
    - Sequence: 8004
    - QKDN: 8005
    - Collector: 8006
  - Topologia simples mantida em memória com NetworkX (`TOPOLOGY_GRAPH` em `orchestrator/app.py`).
  - Comunicação síncrona entre serviços via `requests`. Erros de rede são tratados com prints e listas `success`/`failed` (ver `orchestrator/app.py`).

- Segurança / criptografia:
  - Canal clássico simulado com AES-GCM: helpers em `qnode/secure_channel.py` (`encrypt_aes_gcm`, `decrypt_aes_gcm`).
  - `decrypt_aes_gcm` lança `InvalidTag` quando a autenticação falha — `qnode/app.py` mapeia isso para HTTP 403.
  - Fluxo de chaves: QKDN -> Orchestrator (retorna `key_id`) -> Orchestrator envia `key_hex` para QNodes no payload `/execute_slice`; QNodes armazenam em `NODE_KEYS`.

- Como testar localmente (expectativa dos testes):
  1. Levantar os serviços (cada pasta tem `app.py`):
     ```zsh
     # em cada pasta em terminais separados
     python orchestrator/app.py
     python qnode/app.py 8001   # alice
     python qnode/app.py 8002   # bob
     python sequence/app.py
     python qkdn/app.py
     python collector/app.py
     ```
  2. Rodar o teste E2E que assume as portas acima: `python tests/test_e2e_workflow.py` (usa `requests` e verifica fluxo completo).
  3. Testes dependem de limpar métricas (`DELETE /metrics`) entre execuções — os scripts fazem essa chamada no teste.

- Notas operacionais úteis para um agente:
  - Favor não persistir dados fora dos locais existentes: o projeto usa stores em memória (`METRIC_STORE`, `NODE_KEYS`) e os testes dependem disso.
  - Ao modificar endpoints, mantenha compatibilidade com o payload usado em `tests/test_e2e_workflow.py` (chave `job_id`, `slices`, `key_id`, `key_hex`).
  - Logs são feitos com `print(...)`; manter a mesma abordagem facilita debug automatizado pelo teste.

Se quiser, atualizo para incluir exemplos de requisições curl ou um pequeno script docker-compose para levantar todos os serviços. Há algo específico que ficou vago ou que devo expandir? 
