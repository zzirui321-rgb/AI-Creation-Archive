from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Identifier = Annotated[str, Field(min_length=1, max_length=240)]


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=3000)
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=32)


class Step(Claim):
    node_id: Identifier
    title: str = Field(min_length=1, max_length=200)


class Dependency(Claim):
    source: Identifier
    target: Identifier
    kind: Literal["data", "control", "semantic"]
    confidence: Literal["low", "medium", "high"]


class Variable(Claim):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    fact_id: Identifier
    pointer: str = Field(min_length=1, max_length=1000)


class PathProposal(Claim):
    node_ids: list[Identifier] = Field(max_length=50)


class SemanticDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    intent: Claim
    steps: list[Step] = Field(max_length=50)
    dependencies: list[Dependency] = Field(max_length=150)
    variables: list[Variable] = Field(max_length=50)
    golden_path: PathProposal
    questions: list[Claim] = Field(max_length=30)
