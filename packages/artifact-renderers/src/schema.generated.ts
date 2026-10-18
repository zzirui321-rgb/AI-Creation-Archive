/* Generated from A's packages/schema/src/aica_schema/aica.schema.json. Git fallback: f0fc2942d517c793447140d93670cf5428164a83. Run npm run generate:contract. */

export type Id = string;
export type Time = string;
export type Operation = {
  [k: string]: unknown;
} & {
  variable_id: Id;
  mode: "keep" | "override" | "append";
  value?: unknown;
};
export type NullableId = Id | null;
export type Execution = {
  [k: string]: unknown;
} & {
  kind: "mcp" | "model" | "script" | "command" | "manual";
  tool: string;
  version: string | null;
  model?: string | null;
  inputs: {
    [k: string]: Binding;
  };
  params: {
    [k: string]: unknown;
  };
  outputs: string[];
  runtime_ref?: string;
  /**
   * @minItems 1
   */
  argv?: [string, ...string[]];
  script_artifact_id?: Id;
  cwd?: string;
  permissions?: {
    [k: string]: unknown;
  };
  limits?: {
    [k: string]: unknown;
  };
};
export type Binding =
  | {
      kind: "literal";
      value: unknown;
    }
  | {
      kind: "variable";
      variable_id: Id;
    }
  | {
      kind: "node_output";
      node_id: Id;
      output: string;
    }
  | {
      kind: "artifact";
      artifact_id: Id;
    }
  | {
      kind: "template";
      template: string;
      variables: {
        [k: string]: Id;
      };
    };
export type Ids = Id[];

export interface AicaArchive {
  aica_version: "0.1";
  metadata: {
    project_id: Id;
    title: string;
    creator?: string;
    source_mode: "live_recorded" | "extracted" | "imported" | "mixed";
    created_at: Time;
    updated_at: Time;
    external_links: string[];
    license?: string;
    attribution?: string;
  };
  variables: {
    [k: string]: Variable;
  };
  interactive_questions: Question[];
  variable_operations?: Operation[];
  graph: Graph;
  artifacts: Artifact[];
  harnesses: Harness[];
  agents: Agent[];
  sessions: Session[];
  runs: Run[];
  history?: History[];
  snapshot: Snapshot;
  extensions?: {
    [k: string]: unknown;
  };
}
export interface Variable {
  type: "string" | "array" | "number" | "integer" | "boolean" | "object";
  default: unknown;
  value: unknown;
  description?: string;
  separator?: string;
  /**
   * @minItems 1
   */
  modes: ["keep" | "override" | "append", ...("keep" | "override" | "append")[]];
  bindings: {
    node_id: Id;
    input: string;
  }[];
  items_schema?: {
    [k: string]: unknown;
  };
}
export interface Question {
  id: Id;
  variable_id: Id;
  prompt: string;
  /**
   * @minItems 1
   */
  modes: ["keep" | "override" | "append", ...("keep" | "override" | "append")[]];
}
export interface Graph {
  revision_id: Id;
  parent_revision_id?: NullableId;
  nodes: Node[];
  edges: {
    id: Id;
    source: Id;
    target: Id;
    kind: "data" | "control";
    source_output?: string;
    target_input?: string;
  }[];
  /**
   * @minItems 1
   */
  branches: [
    {
      id: Id;
      parent_id: NullableId;
      state: "active" | "dead_end" | "hidden" | "pruned";
      reason?: string;
    },
    ...{
      id: Id;
      parent_id: NullableId;
      state: "active" | "dead_end" | "hidden" | "pruned";
      reason?: string;
    }[]
  ];
  golden_path: Ids;
}
export interface Node {
  id: Id;
  kind: "prompt" | "decision" | "tool" | "model" | "transform" | "composition";
  label: string;
  branch_id: Id;
  provenance: Provenance;
  execution?: Execution;
  description?: string;
  extensions?: {
    [k: string]: unknown;
  };
}
export interface Provenance {
  mode: "recorded" | "extracted" | "inferred";
  evidence: {
    source: string;
    line?: number;
    commit?: string;
    artifact_id?: Id;
  }[];
  missing?: string[];
}
export interface Artifact {
  id: Id;
  uri: string;
  mime_type: string;
  locations?: string[];
  sha256: string | null;
  size?: number;
  availability: "available" | "unverified" | "missing";
  producer_node_id: NullableId;
  producer_run_id: NullableId;
  preview_uri?: string;
  license?: string;
  attribution?: string;
}
export interface Harness {
  id: Id;
  name: string;
  version: string | null;
  config: {
    [k: string]: unknown;
  };
  state: {
    [k: string]: unknown;
  };
  availability: {
    [k: string]: "recorded" | "missing" | "redacted" | "not_authorized";
  };
  artifact_ids?: Ids;
}
export interface Agent {
  id: Id;
  session_id: Id;
  parent_agent_id: NullableId;
  harness_id: Id;
  client: string;
  model: {
    [k: string]: unknown;
  };
  spawned_by_event_id?: Id;
}
export interface Session {
  id: Id;
  state: "active" | "paused" | "closed";
  created_at: Time;
  consent: {
    record: boolean;
    include_children: boolean;
    decided_at: Time;
  };
}
export interface Run {
  id: Id;
  trace_id: Id;
  node_id: Id;
  revision_id: Id;
  session_id: Id;
  agent_id: Id;
  harness_id: Id;
  state: "running" | "succeeded" | "failed" | "cancelled" | "unknown";
  started_at: Time;
  ended_at?: Time;
  inputs: {
    [k: string]: unknown;
  };
  outputs: {
    [k: string]: unknown;
  };
  artifact_ids: Ids;
  error?: string;
  duration_ms?: number;
  provenance: Provenance;
  cache?: {
    decision?: "CACHE_HIT" | "RECOMPUTE" | "REBUILD" | "unknown";
    input_hash?: string;
    artifact_ids?: Ids;
  };
  preview_stage?: "draft" | "final";
}
export interface History {
  kind: "events" | "revision" | "log";
  artifact_id: Id;
  revision_id?: Id;
  through_seq?: number;
}
export interface Snapshot {
  stream_id: Id;
  snapshot_id: Id;
  commit_seq: number;
  complete: boolean;
  gaps: {
    [k: string]: unknown;
  }[];
}
