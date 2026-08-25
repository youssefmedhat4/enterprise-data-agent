"""Start Wren's MCP service without constructing a database connection."""

from __future__ import annotations

import atexit
import base64
import json
import os
from pathlib import Path
from typing import Any

from mcp.types import ToolAnnotations
from wren import context
from wren.engine import WrenEngine
from wren.mcp_server import ServeContext, build_server
from wren.model.data_source import DataSource
from wren_core import cube_query_to_sql


def main() -> None:
    project = Path(os.environ.get("WREN_PROJECT_PATH", "/project")).resolve()
    manifest = project / "target" / "mdl.json"
    data_source = DataSource(os.environ.get("WREN_DATA_SOURCE", "postgres"))
    engine = WrenEngine(
        manifest_str=base64.b64encode(manifest.read_bytes()).decode(),
        data_source=data_source,
        connection_info={},
    )
    atexit.register(engine.close)
    server_context = ServeContext(
        project=project,
        engine=engine,
        allow_write=False,
        no_connect=True,
    )
    mcp = build_server(server_context)

    @mcp.tool(  # type: ignore[untyped-decorator]
        annotations=ToolAnnotations(title="Translate Cube Query", readOnlyHint=True),
    )
    def translate_cube_query(query: dict[str, Any]) -> dict[str, str]:
        """Translate a structured cube query to physical SQL without a DB connection."""
        mdl_json = json.dumps(context.build_json(project))
        semantic_sql = cube_query_to_sql(json.dumps(query), mdl_json)
        return {
            "sql": engine.dry_plan(semantic_sql),
            "semantic_sql": semantic_sql,
        }

    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = 8080
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
