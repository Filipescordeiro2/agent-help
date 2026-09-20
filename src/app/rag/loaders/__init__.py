"""Loaders de ingestao de fontes de conhecimento (spec FR-012).

Suporta documentos internos fornecidos como texto simples (via API de ingestao) e -- de
forma extensivel -- fontes externas previamente autorizadas, adicionadas como novos loaders
sem alterar o pipeline de chunking/embedding.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LoadedDocument:
    title: str
    source: str
    content: str
    product: str | None = None
    region: str | None = None


def load_from_text(
    *,
    title: str,
    source: str,
    content: str,
    product: str | None = None,
    region: str | None = None,
) -> LoadedDocument:
    """Loader para documentos internos fornecidos como texto (via /knowledge/ingest)."""
    return LoadedDocument(
        title=title, source=source, content=content, product=product, region=region
    )
