"""The complete model-visible tool surface for milestone one."""

TOOLS = [
    {
        "name": "search_repository",
        "description": "Literal text search in approved files at the pinned commit. At most 8 hits.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_repository_file",
        "description": "Read 1–120 lines from an approved file at the pinned commit.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer"},
                "end": {"type": "integer"},
            },
            "required": ["path", "start", "end"],
            "additionalProperties": False,
        },
    },
]
