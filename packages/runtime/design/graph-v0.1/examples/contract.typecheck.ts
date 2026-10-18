import type { GraphDocument, GraphEditRequest, LayoutUpdate } from "../graph.generated";
export const multiturn: GraphDocument = {
  "format": "aica-semantic-graph/0.1",
  "graph_id": "fixture-graph",
  "revision_id": "semantic-revision-1",
  "parent_revision_id": null,
  "repository_branch_id": "repo-main",
  "state": "draft",
  "executable": false,
  "source": {
    "import_id": "fixture-import",
    "archive_sha256": "13ab667b9d19b4a9b145af072e141166c03c77d3fc5b792df36a2c46af203246",
    "archive_revision_id": "raw-revision-1",
    "snapshot_id": "fixture-snapshot",
    "stream_id": "fixture-stream",
    "through_seq": 4
  },
  "parse": {
    "parser_version": "design-0.1",
    "prompt_version": "design-0.1",
    "model": null,
    "mode": "human_annotated_fixture",
    "input_sha256": "13ab667b9d19b4a9b145af072e141166c03c77d3fc5b792df36a2c46af203246"
  },
  "nodes": [
    {
      "id": "step-plan",
      "kind": "intent",
      "label": "确定创作目标",
      "purpose": {
        "text": "用户要求太空漫画与配乐。",
        "attributed_to": "user",
        "actor_id": null,
        "speech_act": "request",
        "evidence_ids": [
          "e-message-1"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "requirements": [],
      "source_node_ids": [
        "raw-plan"
      ],
      "run_ids": [],
      "artifact_ids": [],
      "evidence_ids": [
        "e-node-0",
        "e-message-1"
      ]
    },
    {
      "id": "step-character",
      "kind": "generation",
      "label": "生成并修订角色图",
      "purpose": {
        "text": "Agent 最初选择黑猫与水彩，用户随后保留黑猫并要求红色宇航服。",
        "attributed_to": "analyst",
        "actor_id": null,
        "speech_act": "interpretation",
        "evidence_ids": [
          "e-message-2",
          "e-message-3"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "requirements": [],
      "source_node_ids": [
        "raw-character-first",
        "raw-character-revised"
      ],
      "run_ids": [
        "run-raw-character-first",
        "run-raw-character-revised"
      ],
      "artifact_ids": [
        "artifact-raw-character-revised"
      ],
      "evidence_ids": [
        "e-node-1",
        "e-node-2",
        "e-message-2",
        "e-message-3"
      ]
    },
    {
      "id": "step-panel",
      "kind": "generation",
      "label": "生成月球分镜",
      "purpose": {
        "text": "用户要求使用新角色图绘制月球分镜。",
        "attributed_to": "user",
        "actor_id": null,
        "speech_act": "request",
        "evidence_ids": [
          "e-message-3"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "requirements": [],
      "source_node_ids": [
        "raw-panel"
      ],
      "run_ids": [
        "run-raw-panel"
      ],
      "artifact_ids": [
        "artifact-raw-panel"
      ],
      "evidence_ids": [
        "e-node-3",
        "e-message-3"
      ]
    },
    {
      "id": "step-audio",
      "kind": "generation",
      "label": "生成配乐",
      "purpose": {
        "text": "用户要求为作品配乐。",
        "attributed_to": "user",
        "actor_id": null,
        "speech_act": "request",
        "evidence_ids": [
          "e-message-1"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "requirements": [],
      "source_node_ids": [
        "raw-audio"
      ],
      "run_ids": [
        "run-raw-audio"
      ],
      "artifact_ids": [
        "artifact-raw-audio"
      ],
      "evidence_ids": [
        "e-node-4",
        "e-message-1"
      ]
    }
  ],
  "edges": [
    {
      "kind": "data",
      "id": "edge-character-panel",
      "source": "step-character",
      "target": "step-panel",
      "state": "verified",
      "archive_edge_id": "raw-character-panel",
      "binding": {
        "source_node_id": "raw-character-revised",
        "source_output": "image",
        "target_node_id": "raw-panel",
        "target_input": "reference_image"
      },
      "explanation": {
        "text": "A 定义明确的 image → reference_image 绑定。",
        "attributed_to": "analyst",
        "actor_id": null,
        "speech_act": "interpretation",
        "evidence_ids": [
          "e-node-2",
          "e-node-3"
        ],
        "origin": "human",
        "review": "accepted"
      }
    }
  ],
  "variables": [
    {
      "id": "var-character-prompt",
      "step_id": "step-character",
      "label": "角色生成提示词",
      "description": {
        "text": "可修改修订角色图的提示词。",
        "attributed_to": "analyst",
        "actor_id": null,
        "speech_act": "interpretation",
        "evidence_ids": [
          "e-node-2"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "locator": {
        "source_node_id": "raw-character-revised",
        "evidence_id": "e-node-2",
        "pointer": "/execution/inputs/arguments/value",
        "decode": "json_string",
        "value_pointer": "/prompt"
      },
      "value_type": "string",
      "current_value": "黑猫宇航员，红色宇航服，水彩风格",
      "allowed_modes": [
        "keep",
        "override"
      ],
      "separator": null,
      "review": "candidate"
    }
  ],
  "evidence": [
    {
      "kind": "archive",
      "id": "e-node-0",
      "pointer": "/graph/nodes/0",
      "source_sha256": "13ab667b9d19b4a9b145af072e141166c03c77d3fc5b792df36a2c46af203246"
    },
    {
      "kind": "archive",
      "id": "e-node-1",
      "pointer": "/graph/nodes/1",
      "source_sha256": "13ab667b9d19b4a9b145af072e141166c03c77d3fc5b792df36a2c46af203246"
    },
    {
      "kind": "archive",
      "id": "e-node-2",
      "pointer": "/graph/nodes/2",
      "source_sha256": "13ab667b9d19b4a9b145af072e141166c03c77d3fc5b792df36a2c46af203246"
    },
    {
      "kind": "archive",
      "id": "e-node-3",
      "pointer": "/graph/nodes/3",
      "source_sha256": "13ab667b9d19b4a9b145af072e141166c03c77d3fc5b792df36a2c46af203246"
    },
    {
      "kind": "archive",
      "id": "e-node-4",
      "pointer": "/graph/nodes/4",
      "source_sha256": "13ab667b9d19b4a9b145af072e141166c03c77d3fc5b792df36a2c46af203246"
    },
    {
      "kind": "event",
      "id": "e-message-1",
      "artifact_id": "fixture-history",
      "line": 1,
      "event_id": "event-1",
      "pointer": "/payload/content",
      "source_sha256": "22e4bd6fd8a6b0c73a76db2d04dbb5a171dae2f1d5bcb86743c5116566ef3305"
    },
    {
      "kind": "event",
      "id": "e-message-2",
      "artifact_id": "fixture-history",
      "line": 2,
      "event_id": "event-2",
      "pointer": "/payload/content",
      "source_sha256": "22e4bd6fd8a6b0c73a76db2d04dbb5a171dae2f1d5bcb86743c5116566ef3305"
    },
    {
      "kind": "event",
      "id": "e-message-3",
      "artifact_id": "fixture-history",
      "line": 3,
      "event_id": "event-3",
      "pointer": "/payload/content",
      "source_sha256": "22e4bd6fd8a6b0c73a76db2d04dbb5a171dae2f1d5bcb86743c5116566ef3305"
    },
    {
      "kind": "event",
      "id": "e-message-4",
      "artifact_id": "fixture-history",
      "line": 4,
      "event_id": "event-4",
      "pointer": "/payload/content",
      "source_sha256": "22e4bd6fd8a6b0c73a76db2d04dbb5a171dae2f1d5bcb86743c5116566ef3305"
    }
  ],
  "coverage": {
    "recording_complete": true,
    "selected_source_node_ids": [
      "raw-plan",
      "raw-character-first",
      "raw-character-revised",
      "raw-panel",
      "raw-audio"
    ],
    "unassigned_source_node_ids": [],
    "omitted_history_artifact_ids": [],
    "issues": []
  }
};
export const parallel: GraphDocument = {
  "format": "aica-semantic-graph/0.1",
  "graph_id": "fixture-graph",
  "revision_id": "semantic-revision-1",
  "parent_revision_id": null,
  "repository_branch_id": "repo-main",
  "state": "draft",
  "executable": false,
  "source": {
    "import_id": "fixture-import",
    "archive_sha256": "354bf63c568c23844bef20e640a58e9c6e12440d22dba9136300740dc40e8f19",
    "archive_revision_id": "raw-revision-1",
    "snapshot_id": "fixture-snapshot",
    "stream_id": "fixture-stream",
    "through_seq": 4
  },
  "parse": {
    "parser_version": "design-0.1",
    "prompt_version": "design-0.1",
    "model": null,
    "mode": "human_annotated_fixture",
    "input_sha256": "354bf63c568c23844bef20e640a58e9c6e12440d22dba9136300740dc40e8f19"
  },
  "nodes": [
    {
      "id": "step-plan",
      "kind": "intent",
      "label": "确定创作目标",
      "purpose": {
        "text": "用户要求太空漫画与配乐。",
        "attributed_to": "user",
        "actor_id": null,
        "speech_act": "request",
        "evidence_ids": [
          "e-message-1"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "requirements": [],
      "source_node_ids": [
        "raw-plan"
      ],
      "run_ids": [],
      "artifact_ids": [],
      "evidence_ids": [
        "e-node-0",
        "e-message-1"
      ]
    },
    {
      "id": "step-character",
      "kind": "generation",
      "label": "生成并修订角色图",
      "purpose": {
        "text": "Agent 最初选择黑猫与水彩，用户随后保留黑猫并要求红色宇航服。",
        "attributed_to": "analyst",
        "actor_id": null,
        "speech_act": "interpretation",
        "evidence_ids": [
          "e-message-2",
          "e-message-3"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "requirements": [],
      "source_node_ids": [
        "raw-character-first",
        "raw-character-revised"
      ],
      "run_ids": [
        "run-raw-character-first",
        "run-raw-character-revised"
      ],
      "artifact_ids": [
        "artifact-raw-character-revised"
      ],
      "evidence_ids": [
        "e-node-1",
        "e-node-2",
        "e-message-2",
        "e-message-3"
      ]
    },
    {
      "id": "step-panel",
      "kind": "generation",
      "label": "生成月球分镜",
      "purpose": {
        "text": "用户要求使用新角色图绘制月球分镜。",
        "attributed_to": "user",
        "actor_id": null,
        "speech_act": "request",
        "evidence_ids": [
          "e-message-3"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "requirements": [],
      "source_node_ids": [
        "raw-panel"
      ],
      "run_ids": [
        "run-raw-panel"
      ],
      "artifact_ids": [
        "artifact-raw-panel"
      ],
      "evidence_ids": [
        "e-node-3",
        "e-message-3"
      ]
    },
    {
      "id": "step-audio",
      "kind": "generation",
      "label": "生成配乐",
      "purpose": {
        "text": "用户要求为作品配乐。",
        "attributed_to": "user",
        "actor_id": null,
        "speech_act": "request",
        "evidence_ids": [
          "e-message-1"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "requirements": [],
      "source_node_ids": [
        "raw-audio"
      ],
      "run_ids": [
        "run-raw-audio"
      ],
      "artifact_ids": [
        "artifact-raw-audio"
      ],
      "evidence_ids": [
        "e-node-4",
        "e-message-1"
      ]
    }
  ],
  "edges": [
    {
      "kind": "data",
      "id": "edge-character-panel",
      "source": "step-character",
      "target": "step-panel",
      "state": "verified",
      "archive_edge_id": "raw-character-panel",
      "binding": {
        "source_node_id": "raw-character-revised",
        "source_output": "image",
        "target_node_id": "raw-panel",
        "target_input": "reference_image"
      },
      "explanation": {
        "text": "A 定义明确的 image → reference_image 绑定。",
        "attributed_to": "analyst",
        "actor_id": null,
        "speech_act": "interpretation",
        "evidence_ids": [
          "e-node-2",
          "e-node-3"
        ],
        "origin": "human",
        "review": "accepted"
      }
    }
  ],
  "variables": [
    {
      "id": "var-character-prompt",
      "step_id": "step-character",
      "label": "角色生成提示词",
      "description": {
        "text": "可修改修订角色图的提示词。",
        "attributed_to": "analyst",
        "actor_id": null,
        "speech_act": "interpretation",
        "evidence_ids": [
          "e-node-2"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "locator": {
        "source_node_id": "raw-character-revised",
        "evidence_id": "e-node-2",
        "pointer": "/execution/inputs/arguments/value",
        "decode": "json_string",
        "value_pointer": "/prompt"
      },
      "value_type": "string",
      "current_value": "黑猫宇航员，红色宇航服，水彩风格",
      "allowed_modes": [
        "keep",
        "override"
      ],
      "separator": null,
      "review": "candidate"
    }
  ],
  "evidence": [
    {
      "kind": "archive",
      "id": "e-node-0",
      "pointer": "/graph/nodes/0",
      "source_sha256": "354bf63c568c23844bef20e640a58e9c6e12440d22dba9136300740dc40e8f19"
    },
    {
      "kind": "archive",
      "id": "e-node-1",
      "pointer": "/graph/nodes/1",
      "source_sha256": "354bf63c568c23844bef20e640a58e9c6e12440d22dba9136300740dc40e8f19"
    },
    {
      "kind": "archive",
      "id": "e-node-2",
      "pointer": "/graph/nodes/2",
      "source_sha256": "354bf63c568c23844bef20e640a58e9c6e12440d22dba9136300740dc40e8f19"
    },
    {
      "kind": "archive",
      "id": "e-node-3",
      "pointer": "/graph/nodes/3",
      "source_sha256": "354bf63c568c23844bef20e640a58e9c6e12440d22dba9136300740dc40e8f19"
    },
    {
      "kind": "archive",
      "id": "e-node-4",
      "pointer": "/graph/nodes/4",
      "source_sha256": "354bf63c568c23844bef20e640a58e9c6e12440d22dba9136300740dc40e8f19"
    },
    {
      "kind": "event",
      "id": "e-message-1",
      "artifact_id": "fixture-history",
      "line": 1,
      "event_id": "event-1",
      "pointer": "/payload/content",
      "source_sha256": "22e4bd6fd8a6b0c73a76db2d04dbb5a171dae2f1d5bcb86743c5116566ef3305"
    },
    {
      "kind": "event",
      "id": "e-message-2",
      "artifact_id": "fixture-history",
      "line": 2,
      "event_id": "event-2",
      "pointer": "/payload/content",
      "source_sha256": "22e4bd6fd8a6b0c73a76db2d04dbb5a171dae2f1d5bcb86743c5116566ef3305"
    },
    {
      "kind": "event",
      "id": "e-message-3",
      "artifact_id": "fixture-history",
      "line": 3,
      "event_id": "event-3",
      "pointer": "/payload/content",
      "source_sha256": "22e4bd6fd8a6b0c73a76db2d04dbb5a171dae2f1d5bcb86743c5116566ef3305"
    },
    {
      "kind": "event",
      "id": "e-message-4",
      "artifact_id": "fixture-history",
      "line": 4,
      "event_id": "event-4",
      "pointer": "/payload/content",
      "source_sha256": "22e4bd6fd8a6b0c73a76db2d04dbb5a171dae2f1d5bcb86743c5116566ef3305"
    }
  ],
  "coverage": {
    "recording_complete": true,
    "selected_source_node_ids": [
      "raw-plan",
      "raw-character-first",
      "raw-character-revised",
      "raw-panel",
      "raw-audio"
    ],
    "unassigned_source_node_ids": [],
    "omitted_history_artifact_ids": [],
    "issues": []
  }
};
export const partial: GraphDocument = {
  "format": "aica-semantic-graph/0.1",
  "graph_id": "fixture-graph",
  "revision_id": "semantic-revision-1",
  "parent_revision_id": null,
  "repository_branch_id": "repo-main",
  "state": "draft",
  "executable": false,
  "source": {
    "import_id": "fixture-import",
    "archive_sha256": "a42c39e20b39b4b8bab5ef81dfe953e4172e9d33a25c1c395e8b7bfd30310e8c",
    "archive_revision_id": "raw-revision-1",
    "snapshot_id": "fixture-snapshot",
    "stream_id": "fixture-stream",
    "through_seq": 4
  },
  "parse": {
    "parser_version": "design-0.1",
    "prompt_version": "design-0.1",
    "model": null,
    "mode": "human_annotated_fixture",
    "input_sha256": "a42c39e20b39b4b8bab5ef81dfe953e4172e9d33a25c1c395e8b7bfd30310e8c"
  },
  "nodes": [
    {
      "id": "step-plan",
      "kind": "intent",
      "label": "确定创作目标",
      "purpose": {
        "text": "用户要求太空漫画与配乐。",
        "attributed_to": "user",
        "actor_id": null,
        "speech_act": "request",
        "evidence_ids": [
          "e-message-1"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "requirements": [],
      "source_node_ids": [
        "raw-plan"
      ],
      "run_ids": [],
      "artifact_ids": [],
      "evidence_ids": [
        "e-node-0",
        "e-message-1"
      ]
    },
    {
      "id": "step-character",
      "kind": "generation",
      "label": "生成并修订角色图",
      "purpose": {
        "text": "Agent 最初选择黑猫与水彩，用户随后保留黑猫并要求红色宇航服。",
        "attributed_to": "analyst",
        "actor_id": null,
        "speech_act": "interpretation",
        "evidence_ids": [
          "e-message-2",
          "e-message-3"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "requirements": [],
      "source_node_ids": [
        "raw-character-first",
        "raw-character-revised"
      ],
      "run_ids": [
        "run-raw-character-first",
        "run-raw-character-revised"
      ],
      "artifact_ids": [
        "artifact-raw-character-revised"
      ],
      "evidence_ids": [
        "e-node-1",
        "e-node-2",
        "e-message-2",
        "e-message-3"
      ]
    },
    {
      "id": "step-panel",
      "kind": "generation",
      "label": "生成月球分镜",
      "purpose": {
        "text": "用户要求使用新角色图绘制月球分镜。",
        "attributed_to": "user",
        "actor_id": null,
        "speech_act": "request",
        "evidence_ids": [
          "e-message-3"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "requirements": [],
      "source_node_ids": [
        "raw-panel"
      ],
      "run_ids": [
        "run-raw-panel"
      ],
      "artifact_ids": [
        "artifact-raw-panel"
      ],
      "evidence_ids": [
        "e-node-3",
        "e-message-3"
      ]
    },
    {
      "id": "step-audio",
      "kind": "generation",
      "label": "生成配乐",
      "purpose": {
        "text": "用户要求为作品配乐。",
        "attributed_to": "user",
        "actor_id": null,
        "speech_act": "request",
        "evidence_ids": [
          "e-message-1"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "requirements": [],
      "source_node_ids": [
        "raw-audio"
      ],
      "run_ids": [
        "run-raw-audio"
      ],
      "artifact_ids": [
        "artifact-raw-audio"
      ],
      "evidence_ids": [
        "e-node-4",
        "e-message-1"
      ]
    }
  ],
  "edges": [
    {
      "kind": "data",
      "id": "edge-character-panel",
      "source": "step-character",
      "target": "step-panel",
      "state": "verified",
      "archive_edge_id": "raw-character-panel",
      "binding": {
        "source_node_id": "raw-character-revised",
        "source_output": "image",
        "target_node_id": "raw-panel",
        "target_input": "reference_image"
      },
      "explanation": {
        "text": "A 定义明确的 image → reference_image 绑定。",
        "attributed_to": "analyst",
        "actor_id": null,
        "speech_act": "interpretation",
        "evidence_ids": [
          "e-node-2",
          "e-node-3"
        ],
        "origin": "human",
        "review": "accepted"
      }
    }
  ],
  "variables": [
    {
      "id": "var-character-prompt",
      "step_id": "step-character",
      "label": "角色生成提示词",
      "description": {
        "text": "可修改修订角色图的提示词。",
        "attributed_to": "analyst",
        "actor_id": null,
        "speech_act": "interpretation",
        "evidence_ids": [
          "e-node-2"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "locator": {
        "source_node_id": "raw-character-revised",
        "evidence_id": "e-node-2",
        "pointer": "/execution/inputs/arguments/value",
        "decode": "json_string",
        "value_pointer": "/prompt"
      },
      "value_type": "string",
      "current_value": "黑猫宇航员，红色宇航服，水彩风格",
      "allowed_modes": [
        "keep",
        "override"
      ],
      "separator": null,
      "review": "candidate"
    }
  ],
  "evidence": [
    {
      "kind": "archive",
      "id": "e-node-0",
      "pointer": "/graph/nodes/0",
      "source_sha256": "a42c39e20b39b4b8bab5ef81dfe953e4172e9d33a25c1c395e8b7bfd30310e8c"
    },
    {
      "kind": "archive",
      "id": "e-node-1",
      "pointer": "/graph/nodes/1",
      "source_sha256": "a42c39e20b39b4b8bab5ef81dfe953e4172e9d33a25c1c395e8b7bfd30310e8c"
    },
    {
      "kind": "archive",
      "id": "e-node-2",
      "pointer": "/graph/nodes/2",
      "source_sha256": "a42c39e20b39b4b8bab5ef81dfe953e4172e9d33a25c1c395e8b7bfd30310e8c"
    },
    {
      "kind": "archive",
      "id": "e-node-3",
      "pointer": "/graph/nodes/3",
      "source_sha256": "a42c39e20b39b4b8bab5ef81dfe953e4172e9d33a25c1c395e8b7bfd30310e8c"
    },
    {
      "kind": "archive",
      "id": "e-node-4",
      "pointer": "/graph/nodes/4",
      "source_sha256": "a42c39e20b39b4b8bab5ef81dfe953e4172e9d33a25c1c395e8b7bfd30310e8c"
    },
    {
      "kind": "event",
      "id": "e-message-1",
      "artifact_id": "fixture-history",
      "line": 1,
      "event_id": "event-1",
      "pointer": "/payload/content",
      "source_sha256": "22e4bd6fd8a6b0c73a76db2d04dbb5a171dae2f1d5bcb86743c5116566ef3305"
    },
    {
      "kind": "event",
      "id": "e-message-2",
      "artifact_id": "fixture-history",
      "line": 2,
      "event_id": "event-2",
      "pointer": "/payload/content",
      "source_sha256": "22e4bd6fd8a6b0c73a76db2d04dbb5a171dae2f1d5bcb86743c5116566ef3305"
    },
    {
      "kind": "event",
      "id": "e-message-3",
      "artifact_id": "fixture-history",
      "line": 3,
      "event_id": "event-3",
      "pointer": "/payload/content",
      "source_sha256": "22e4bd6fd8a6b0c73a76db2d04dbb5a171dae2f1d5bcb86743c5116566ef3305"
    },
    {
      "kind": "event",
      "id": "e-message-4",
      "artifact_id": "fixture-history",
      "line": 4,
      "event_id": "event-4",
      "pointer": "/payload/content",
      "source_sha256": "22e4bd6fd8a6b0c73a76db2d04dbb5a171dae2f1d5bcb86743c5116566ef3305"
    }
  ],
  "coverage": {
    "recording_complete": false,
    "selected_source_node_ids": [
      "raw-plan",
      "raw-character-first",
      "raw-character-revised",
      "raw-panel",
      "raw-audio"
    ],
    "unassigned_source_node_ids": [],
    "omitted_history_artifact_ids": [],
    "issues": [
      {
        "id": "issue-gap",
        "code": "SOURCE_GAP",
        "severity": "warning",
        "message": "源录制存在序号缺口，不能宣称完整。",
        "node_ids": [],
        "evidence_ids": []
      }
    ]
  }
};
export const edit: GraphEditRequest = {
  "request_id": "edit-request-1",
  "expected_revision_id": "semantic-revision-1",
  "operations": [
    {
      "op": "set_label",
      "node_id": "step-character",
      "value": "角色参考图"
    }
  ]
};
export const layout: LayoutUpdate = {
  "request_id": "layout-request-1",
  "graph_revision_id": "semantic-revision-1",
  "expected_layout_version": 0,
  "positions": [
    {
      "node_id": "step-character",
      "x": 200.0,
      "y": 80.0
    }
  ]
};
