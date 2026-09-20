"""Definicoes do agente versionadas em arquivos: Skills, Playbooks e Keywords (YAML).

Os arquivos desta pasta sao a fonte de verdade da *configuracao* do agente de atendimento N1:
no boot da aplicacao `sync_definitions` grava/atualiza esse conteudo no MongoDB e gera os
embeddings (ver `sync.py`). O conteudo pode ser sobrescrito por outra pasta via
`AGENT_DEFINITIONS_DIR`, e desligado com `AGENT_DEFINITIONS_ENABLED=false`.
"""
