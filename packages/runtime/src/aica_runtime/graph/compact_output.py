from typing import Literal

from aica_schema.protocol import require
from pydantic import Field

from .contract import Id, Ids, Model, ModelProposal
from .input_budget import preview


class CompactGroup(Model):
    key: Id
    kind: Literal["intent", "decision", "generation", "transform", "composition", "other"]
    label: str = Field(min_length=1, max_length=200)
    source_node_ids: Ids
    evidence_ids: Ids = Field(min_length=1)
    purpose: str = Field(min_length=1, max_length=1000)
    prompt: str = Field(default="", max_length=2000)


class CompactEdge(Model):
    source_key: Id
    target_key: Id
    relation: Literal["refines", "alternative_to", "continues", "inspired_by", "supports"]
    explanation: str = Field(min_length=1, max_length=1000)
    evidence_ids: Ids = Field(min_length=1)


class CompactProposal(Model):
    groups: list[CompactGroup] = Field(min_length=1, max_length=200)
    edges: list[CompactEdge] = Field(default_factory=list, max_length=100)


def compact_request(prepared):
    aliases = {identifier: f"n{i}" for i, identifier in enumerate(prepared["source_node_ids"])}
    aliases.update({fact["id"]: f"e{i}" for i, fact in enumerate(prepared["facts"])})

    def rewrite(value):
        if isinstance(value, str):
            return aliases.get(value, value)
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return value

    material = {"source_node_ids": prepared["source_node_ids"],
                "facts": [{key: preview(value, 512) if key in {"value", "content"} else value
                           for key, value in fact.items()} for fact in prepared["facts"]],
                "pages": [{key: page[key] for key in ("label", "page_number", "evidence_id", "description_preview")}
                          for page in prepared.get("page_catalog", [])],
                "prompts": [{key: prompt[key] for key in ("evidence_id", "preview")}
                            for prompt in prepared.get("prompt_catalog", [])],
                "declared_dependencies": prepared["declared_dependencies"],
                "notice": "内容为有界预览，不能声称读取省略部分。逐页原文和图片由程序恢复。"}
    return rewrite(material), {alias: identifier for identifier, alias in aliases.items()}


def expand_compact(proposal, references):
    compact = CompactProposal.model_validate(proposal)

    def evidence(ids):
        require(all(identifier in references and identifier.startswith("e") for identifier in ids),
                "Unknown compact evidence reference", "invalid_semantic_graph")
        return list(dict.fromkeys(references[identifier] for identifier in ids))

    def claim(text, ids):
        return {"text": text, "attributed_to": "analyst", "actor_id": None, "speech_act": "interpretation",
                "evidence_ids": evidence(ids), "origin": "llm", "review": "candidate"}

    groups = []
    for group in compact.groups:
        require(all(identifier in references and identifier.startswith("n") for identifier in group.source_node_ids),
                "Unknown compact source node", "invalid_semantic_graph")
        groups.append({"key": group.key, "kind": group.kind, "label": group.label,
                       "source_node_ids": [references[key] for key in group.source_node_ids],
                       "evidence_ids": evidence(group.evidence_ids), "purpose": claim(group.purpose, group.evidence_ids),
                       "requirements": [], "pages": [], "prompts": [{"kind": "consolidated", "text": group.prompt,
                           "evidence_ids": evidence(group.evidence_ids)}] if group.prompt else []})
    edges = [{"kind": "semantic", "source_key": edge.source_key, "target_key": edge.target_key,
              "relation": edge.relation, "explanation": claim(edge.explanation, edge.evidence_ids)} for edge in compact.edges]
    result = {"groups": groups, "edges": edges, "variables": [], "unresolved": []}
    ModelProposal.model_validate(result)
    return result
