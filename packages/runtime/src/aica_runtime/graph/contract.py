from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

Id = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
Ids = Annotated[list[Id], Field(max_length=1000)]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=3000)]
Role = Literal["user", "agent", "tool", "analyst", "unknown"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Source(Model):
    import_id: Id
    archive_sha256: Sha256
    archive_revision_id: Id
    snapshot_id: Id
    stream_id: Id
    through_seq: int = Field(ge=0)


class ArchiveEvidence(Model):
    kind: Literal["archive"]
    id: Id
    pointer: Annotated[str, Field(pattern=r"^/")]
    source_sha256: Sha256


class EventEvidence(Model):
    kind: Literal["event"]
    id: Id
    artifact_id: Id
    line: int = Field(ge=1)
    event_id: Id
    pointer: Annotated[str, Field(pattern=r"^/")]
    source_sha256: Sha256


Evidence = Annotated[ArchiveEvidence | EventEvidence, Field(discriminator="kind")]


class Claim(Model):
    text: Text
    attributed_to: Role
    actor_id: Id | None
    speech_act: Literal["request", "proposal", "observation", "interpretation"]
    evidence_ids: Annotated[Ids, Field(min_length=1)]
    origin: Literal["rule", "llm", "human"]
    review: Literal["candidate", "accepted", "rejected"]


class ModulePrompt(Model):
    text: Annotated[str, Field(min_length=1)]
    kind: Literal["recorded", "consolidated", "suggested", "edited"]
    evidence_ids: Annotated[Ids, Field(min_length=1)]
    source_pointer: str | None = None


class ModulePage(Model):
    id: Id
    label: Annotated[str, Field(min_length=1, max_length=200)]
    page_number: int | None = Field(default=None, ge=1)
    summary: Claim
    prompts: list[ModulePrompt] = Field(default_factory=list, max_length=200)
    artifact_ids: Ids
    evidence_ids: Annotated[Ids, Field(min_length=1)]


class ModuleImageEdit(Model):
    id: Id
    original_artifact_id: Id
    result_artifact_id: Id
    instruction: Annotated[str, Field(min_length=1, max_length=4000)]
    region: list[float] | None = None
    model: str
    source: str


class Step(Model):
    id: Id
    kind: Literal["intent", "decision", "generation", "transform", "composition", "other"]
    label: Annotated[str, Field(min_length=1, max_length=200)]
    purpose: Claim
    requirements: list[Claim] = Field(max_length=50)
    source_node_ids: Ids
    run_ids: Ids
    artifact_ids: Ids
    evidence_ids: Annotated[Ids, Field(min_length=1)]
    prompts: list[ModulePrompt] = Field(default_factory=list, max_length=200)
    pages: list[ModulePage] = Field(default_factory=list, max_length=200)
    image_edits: list[ModuleImageEdit] = Field(default_factory=list, max_length=200)


class DataBinding(Model):
    source_node_id: Id
    source_output: Id
    target_node_id: Id
    target_input: Id


class DataEdge(Model):
    kind: Literal["data"]
    id: Id
    source: Id
    target: Id
    state: Literal["candidate", "verified"]
    archive_edge_id: Id | None
    binding: DataBinding | None
    explanation: Claim


class ControlEdge(Model):
    kind: Literal["control"]
    id: Id
    source: Id
    target: Id
    state: Literal["candidate", "verified"]
    archive_edge_id: Id | None
    explanation: Claim


class SemanticEdge(Model):
    kind: Literal["semantic"]
    id: Id
    source: Id
    target: Id
    relation: Literal["refines", "alternative_to", "continues", "inspired_by", "supports"]
    explanation: Claim


Edge = Annotated[DataEdge | ControlEdge | SemanticEdge, Field(discriminator="kind")]


class ValueLocator(Model):
    source_node_id: Id
    evidence_id: Id
    pointer: Annotated[str, Field(pattern=r"^/")]
    decode: Literal["identity", "json_string"]
    value_pointer: str


class Variable(Model):
    id: Id
    step_id: Id
    label: Annotated[str, Field(min_length=1, max_length=200)]
    description: Claim
    locator: ValueLocator
    value_type: Literal["string", "array", "number", "integer", "boolean", "object"]
    current_value: JsonValue
    allowed_modes: Annotated[list[Literal["keep", "override", "append"]], Field(min_length=1, max_length=3)]
    separator: str | None
    review: Literal["candidate", "accepted", "rejected"]


class Issue(Model):
    id: Id
    code: Id
    severity: Literal["warning", "error"]
    message: Text
    node_ids: Ids
    evidence_ids: Ids


class Coverage(Model):
    recording_complete: bool
    selected_source_node_ids: Ids
    unassigned_source_node_ids: Ids
    omitted_history_artifact_ids: Ids
    issues: list[Issue] = Field(max_length=1000)


class ParseInfo(Model):
    parser_version: Text
    prompt_version: Text
    model: str | None
    mode: Literal["rules_only", "rules_llm", "human_annotated_fixture"]
    input_sha256: Sha256


class GraphDocument(Model):
    format: Literal["aica-semantic-graph/0.1"]
    graph_id: Id
    revision_id: Id
    parent_revision_id: Id | None
    repository_branch_id: Id
    state: Literal["draft", "accepted"]
    executable: Literal[False]
    source: Source
    parse: ParseInfo
    nodes: list[Step] = Field(max_length=200)
    edges: list[Edge] = Field(max_length=1000)
    variables: list[Variable] = Field(max_length=200)
    evidence: list[Evidence] = Field(max_length=2000)
    coverage: Coverage


class LabelEdit(Model):
    op: Literal["set_label"]
    node_id: Id
    value: Annotated[str, Field(min_length=1, max_length=200)]


class ClaimEdit(Model):
    op: Literal["replace_purpose"]
    node_id: Id
    value: Claim


class EdgeEdit(Model):
    op: Literal["upsert_edge"]
    value: Edge


class DeleteEdge(Model):
    op: Literal["delete_edge"]
    edge_id: Id


class ReviewVariable(Model):
    op: Literal["review_variable"]
    variable_id: Id
    review: Literal["accepted", "rejected"]


class ReplaceContent(Model):
    op: Literal["replace_content"]
    nodes: list[Step] = Field(max_length=200)
    edges: list[Edge] = Field(max_length=1000)
    variables: list[Variable] = Field(max_length=200)


Edit = Annotated[LabelEdit | ClaimEdit | EdgeEdit | DeleteEdge | ReviewVariable | ReplaceContent,
                 Field(discriminator="op")]


class GraphEditRequest(Model):
    request_id: Id
    expected_revision_id: Id
    operations: Annotated[list[Edit], Field(min_length=1, max_length=100)]


class LayoutPosition(Model):
    node_id: Id
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)


class LayoutUpdate(Model):
    request_id: Id
    graph_revision_id: Id
    expected_layout_version: int = Field(ge=0)
    positions: list[LayoutPosition] = Field(max_length=200)


class ProposalClaim(Claim):
    origin: Literal["llm"]
    review: Literal["candidate"]


class PromptProposal(Model):
    text: Annotated[str, Field(min_length=1, max_length=6000)]
    kind: Literal["consolidated", "suggested"]
    evidence_ids: Annotated[Ids, Field(min_length=1)]


class PagePromptProposal(Model):
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    kind: Literal["consolidated", "suggested"]


class PageProposal(Model):
    source_page_id: Id
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    prompts: list[PagePromptProposal] = Field(default_factory=list, max_length=3)


class GroupProposal(Model):
    key: Id
    kind: Literal["intent", "decision", "generation", "transform", "composition", "other"]
    label: Annotated[str, Field(min_length=1, max_length=200)]
    source_node_ids: Ids
    purpose: ProposalClaim
    requirements: list[ProposalClaim] = Field(max_length=50)
    evidence_ids: Annotated[Ids, Field(min_length=1)]
    prompt_ids: Ids = Field(default_factory=list)
    prompts: list[PromptProposal] = Field(default_factory=list, max_length=8)
    pages: list[PageProposal] = Field(default_factory=list, max_length=200)


class DependencyProposal(Model):
    source_key: Id
    target_key: Id
    kind: Literal["data", "control"]
    explanation: ProposalClaim


class SemanticProposal(Model):
    source_key: Id
    target_key: Id
    kind: Literal["semantic"]
    relation: Literal["refines", "alternative_to", "continues", "inspired_by", "supports"]
    explanation: ProposalClaim


EdgeProposal = Annotated[DependencyProposal | SemanticProposal, Field(discriminator="kind")]


class VariableProposal(Model):
    group_key: Id
    label: Annotated[str, Field(min_length=1, max_length=200)]
    locator: ValueLocator
    description: ProposalClaim


class ModelProposal(Model):
    groups: list[GroupProposal] = Field(max_length=200)
    edges: list[EdgeProposal] = Field(max_length=1000)
    variables: list[VariableProposal] = Field(max_length=200)
    unresolved: list[Issue] = Field(max_length=1000)
