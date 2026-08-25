from app.data.gateway import TableMetadata
from app.governance.gateway import GovernanceGateway, GovernanceSnapshot


class DisabledGovernanceGateway(GovernanceGateway):
    async def get_metadata(self, tables: list[TableMetadata]) -> GovernanceSnapshot:
        del tables
        return GovernanceSnapshot(provider="disabled")

    async def close(self) -> None:
        return None
