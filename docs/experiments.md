# Experimentos da Arquitetura DQC-QKD

## 1. Quantum Teleportation
- Nós: Alice e Bob
- Ações:
  1. SeQUeNCe cria par EPR (fidelidade ~0.92)
  2. Alice mede e envia bits via canal QKD
  3. Bob aplica operação condicional
- Métricas: F, Lₑ, QBER

## 2. Entanglement Swapping
- Nós: Alice, Charlie, Bob
- Objetivo: validar entanglement multi-hop
- Métricas: fidelidade, latência total

## 3. Quantum Secret Sharing (QSS)
- Nós: Alice, Bob, Charlie
- Protocolo: estado GHZ + cooperação clássica
- Métricas: fidelidade global, taxa de sucesso, QBER
