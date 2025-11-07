# DQC-QKD: Simulação de Computação Quântica Distribuída com QKD
# Módulos da Arquitetura DQC-QKD

## Orchestrator
Coordena execução distribuída e sincroniza comunicação quântica e clássica.
- Recebe circuitos fatiados.
- Solicita chaves ao QKDN.
- Solicita pares EPR ao SeQUeNCe.
- Gerencia feed-forward clássico.
- Armazena resultados no Collector.

## QKDN
Stub compatível ETSI QKD 014.
- Endpoint: /qkd/v1/keys
- Retorna pares { key_id, key_data_hex }.
- Gera chaves pseudoaleatórias com `secrets.token_bytes`.

## SeQUeNCe
Simula rede quântica de múltiplos nós.
- Modela canais, atrasos e fidelidade.
- Notifica Orchestrator quando pares EPR estão prontos.

## QNode
Executa subcircuitos locais via Qiskit AerSimulator.
- Recebe instruções via REST.
- Implementa feed-forward condicional com chaves QKD.
- Usa AES-GCM para comunicações seguras (secure_channel.py).

## Collector
Recebe e armazena métricas de execução.
- Endpoints: `/metrics`, `/reset`
- Dados: fidelidade, latência, QBER.

