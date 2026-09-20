"""Leitura e validacao dos arquivos de definicao (Skills, Playbooks, Keywords) -- YAML.

Estrutura esperada dentro da pasta de definicoes:

    skills/*.yaml      um arquivo por Skill
    playbooks/*.yaml   um arquivo por Playbook
    keywords/*.yaml    lista `keywords:` (um ou mais arquivos)
    web_sources/*.yaml lista `sources:` (fontes web padrao do agente)

A validacao e completa e falha com TODOS os problemas de uma vez (`DefinitionsError`): ids unicos,
referencias entre keywords/skills/playbooks existentes, passos de Playbook apontando para passos
que existem, e ferramentas realmente registradas.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from app.config.settings import get_settings

DEFAULT_DIR = Path(__file__).resolve().parent
_PLAYBOOK_TERMINAL_TARGETS = {"closed", "escalate"}


class DefinitionsError(Exception):
    """Definicoes invalidas; `problems` lista cada problema encontrado."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("definicoes invalidas:\n- " + "\n- ".join(problems))
        self.problems = problems


class SkillDef(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    owning_agent: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    enabled: bool = True
    always_apply: bool = False


class StepDef(BaseModel):
    step_id: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    tool: str | None = None
    on_success: str | None = None
    on_failure: str | None = None


class PlaybookDef(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    owning_agent: str = "customer_support_agent"
    status: str = "active"
    symptoms: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    steps: list[StepDef] = Field(default_factory=list)
    decision_points: list[dict[str, Any]] = Field(default_factory=list)
    authorized_tools: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    escalation_rules: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    closure_criteria: list[str] = Field(default_factory=list)
    # Playbook PRINCIPAL: vale para toda solicitacao do agente e orienta quando usar cada
    # sub-playbook (o do assunto e somado a ele).
    always_apply: bool = False
    # SUB-PLAYBOOK: id do Playbook principal a que pertence + quando o principal deve usa-lo.
    parent: str | None = None
    when_to_use: str | None = None
    # Skills (ids) que ajudam neste Playbook; entram no prompt junto com as do assunto.
    skills: list[str] = Field(default_factory=list)


class KeywordDef(BaseModel):
    id: str = Field(min_length=1)
    term: str = Field(min_length=1)
    synonyms: list[str] = Field(default_factory=list)
    weight: float = Field(default=1.0, gt=0)
    related_intents: list[str] = Field(default_factory=list)
    associated_documents: list[str] = Field(default_factory=list)
    associated_skills: list[str] = Field(default_factory=list)
    associated_playbooks: list[str] = Field(default_factory=list)


class WebSourceDef(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    description: str = Field(min_length=1)
    usage: str = Field(min_length=1)
    topics: list[str] = Field(default_factory=list)
    priority: int = 100
    enabled: bool = True


class Definitions(BaseModel):
    skills: list[SkillDef] = Field(default_factory=list)
    playbooks: list[PlaybookDef] = Field(default_factory=list)
    keywords: list[KeywordDef] = Field(default_factory=list)
    web_sources: list[WebSourceDef] = Field(default_factory=list)


def content_hash(definition: BaseModel) -> str:
    """Hash estavel do conteudo -- decide se o registro no banco precisa ser atualizado."""
    canonical = json.dumps(definition.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def definitions_dir() -> Path:
    configured = get_settings().agent_definitions_dir
    return Path(configured) if configured else DEFAULT_DIR


def _read_yaml(path: Path, problems: list[str]) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        problems.append(f"{path.name}: nao foi possivel ler o YAML ({type(exc).__name__})")
        return None


def _parse(model: type[BaseModel], raw: Any, where: str, problems: list[str]):
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            problems.append(f"{where}: {location or 'documento'} -> {error['msg']}")
        return None


def _known_tool_names() -> set[str]:
    from app.tools import customer_support_tools, knowledge_tools, web_tools  # noqa: F401
    from app.tools.base import BaseTool

    names: set[str] = set()
    pending = list(BaseTool.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        name = getattr(cls, "name", None)
        if isinstance(name, str):
            names.add(name)
    return names


def _validate_cross_references(defs: Definitions, problems: list[str]) -> None:
    def duplicated(kind: str, ids: list[str]) -> None:
        for value in {i for i in ids if ids.count(i) > 1}:
            problems.append(f"{kind}: id duplicado '{value}'")

    duplicated("skill", [s.id for s in defs.skills])
    duplicated("playbook", [p.id for p in defs.playbooks])
    duplicated("keyword", [k.id for k in defs.keywords])
    terms = [k.term.strip().lower() for k in defs.keywords]
    for term in {t for t in terms if terms.count(t) > 1}:
        problems.append(f"keyword: term duplicado '{term}'")

    duplicated("web_source", [w.id for w in defs.web_sources])
    from urllib.parse import urlsplit

    from app.repository.web_sources_repository import normalize_url

    urls = []
    for source in defs.web_sources:
        if urlsplit(source.url).scheme not in ("http", "https"):
            problems.append(f"web_source {source.id}: url deve ser http(s)")
        urls.append(normalize_url(source.url))
    for value in {u for u in urls if urls.count(u) > 1}:
        problems.append(f"web_source: url duplicada '{value}'")

    skill_ids = {s.id for s in defs.skills}
    playbook_ids = {p.id for p in defs.playbooks}
    for keyword in defs.keywords:
        for skill_id in keyword.associated_skills:
            if skill_id not in skill_ids:
                problems.append(f"keyword {keyword.id}: skill inexistente '{skill_id}'")
        for playbook_id in keyword.associated_playbooks:
            if playbook_id not in playbook_ids:
                problems.append(f"keyword {keyword.id}: playbook inexistente '{playbook_id}'")

    main_ids = {p.id for p in defs.playbooks if p.always_apply}
    for playbook in defs.playbooks:
        if playbook.parent is not None:
            if playbook.parent not in main_ids:
                problems.append(
                    f"playbook {playbook.id}: parent '{playbook.parent}' nao e um Playbook "
                    "principal (always_apply)"
                )
            if not playbook.when_to_use:
                problems.append(
                    f"playbook {playbook.id}: sub-playbook precisa de 'when_to_use' (quando o "
                    "principal deve usa-lo)"
                )
    tools = _known_tool_names()
    for playbook in defs.playbooks:
        for skill_id in playbook.skills:
            if skill_id not in skill_ids:
                problems.append(f"playbook {playbook.id}: skill inexistente '{skill_id}'")
        step_ids = [s.step_id for s in playbook.steps]
        for value in {i for i in step_ids if step_ids.count(i) > 1}:
            problems.append(f"playbook {playbook.id}: step_id duplicado '{value}'")
        valid_targets = set(step_ids) | _PLAYBOOK_TERMINAL_TARGETS
        for step in playbook.steps:
            for target in (step.on_success, step.on_failure):
                if target is not None and target not in valid_targets:
                    problems.append(
                        f"playbook {playbook.id}: passo '{step.step_id}' aponta para "
                        f"'{target}', que nao existe"
                    )
            if step.tool and step.tool not in playbook.authorized_tools:
                problems.append(
                    f"playbook {playbook.id}: passo '{step.step_id}' usa a ferramenta "
                    f"'{step.tool}' que nao esta em authorized_tools"
                )
        for tool in {*playbook.authorized_tools, *(s.tool for s in playbook.steps if s.tool)}:
            if tool not in tools:
                problems.append(f"playbook {playbook.id}: ferramenta desconhecida '{tool}'")
    for skill in defs.skills:
        for tool in skill.allowed_tools:
            if tool not in tools:
                problems.append(f"skill {skill.id}: ferramenta desconhecida '{tool}'")


def load_definitions(directory: Path | None = None) -> Definitions:
    """Le e valida todas as definicoes; levanta `DefinitionsError` listando os problemas."""
    root = directory or definitions_dir()
    problems: list[str] = []
    defs = Definitions()

    for path in sorted((root / "skills").glob("*.y*ml")):
        raw = _read_yaml(path, problems)
        if raw is not None and (parsed := _parse(SkillDef, raw, path.name, problems)):
            defs.skills.append(parsed)
    for path in sorted((root / "playbooks").glob("*.y*ml")):
        raw = _read_yaml(path, problems)
        if raw is not None and (parsed := _parse(PlaybookDef, raw, path.name, problems)):
            defs.playbooks.append(parsed)
    for path in sorted((root / "keywords").glob("*.y*ml")):
        raw = _read_yaml(path, problems)
        if not isinstance(raw, dict) or not isinstance(raw.get("keywords"), list):
            problems.append(f"{path.name}: esperado uma lista em 'keywords:'")
            continue
        for index, item in enumerate(raw["keywords"]):
            if parsed := _parse(KeywordDef, item, f"{path.name}[{index}]", problems):
                defs.keywords.append(parsed)

    for path in sorted((root / "web_sources").glob("*.y*ml")):
        raw = _read_yaml(path, problems)
        if not isinstance(raw, dict) or not isinstance(raw.get("sources"), list):
            problems.append(f"{path.name}: esperado uma lista em 'sources:'")
            continue
        for index, item in enumerate(raw["sources"]):
            if parsed := _parse(WebSourceDef, item, f"{path.name}[{index}]", problems):
                defs.web_sources.append(parsed)

    _validate_cross_references(defs, problems)
    if problems:
        raise DefinitionsError(problems)
    return defs
