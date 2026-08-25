from app.config import Settings
from app.semantic.gateway import SemanticGateway
from app.semantic.in_memory import InMemorySemanticGateway
from app.semantic.wren import MCPWrenContextClient, WrenSemanticGateway


def build_semantic_gateway(settings: Settings) -> SemanticGateway:
    if settings.semantic_provider == "inmemory":
        return InMemorySemanticGateway()
    return WrenSemanticGateway(
        MCPWrenContextClient(
            settings.wren_mcp_url,
            timeout_seconds=settings.wren_timeout_seconds,
        ),
        max_models=settings.wren_max_context_models,
        project_id=settings.wren_project_id,
    )
