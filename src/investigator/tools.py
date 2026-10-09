"""The complete model-visible tool surface for milestone one."""

TOOLS = [
    {
        "name": "search_repository",
        "description": "Literal text search in an approved pinned repository. Use * to search all repository roles. At most 8 hits.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "repository": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["repository", "query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_repository_file",
        "description": "Read 1–120 lines from an approved file in a named repository role.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "repository": {"type": "string"},
                "path": {"type": "string"},
                "start": {"type": "integer"},
                "end": {"type": "integer"},
            },
            "required": ["repository", "path", "start", "end"],
            "additionalProperties": False,
        },
    },
]
