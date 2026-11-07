"""Tool schemas — the only thing the model reads about write-gate."""

CHECK = {
    "name": "write_gate_check",
    "description": (
        "Validate file content BEFORE writing it. Deterministically parses JSON, "
        "TOML and Python, rejects NUL bytes, oversized content and any configured "
        "regex contracts, and returns exact line/column errors. Use this to "
        "preflight a file you are about to create or edit; if it reports issues, "
        "fix the content first. Also available as /writegate."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Destination path including extension, e.g. 'config/app.json'. "
                    "The extension selects the syntax checker."
                ),
            },
            "content": {
                "type": "string",
                "description": "The exact content you intend to write.",
            },
        },
        "required": ["path", "content"],
    },
}
