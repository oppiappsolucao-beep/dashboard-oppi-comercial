from config.settings import settings


class ProposalService:
    def next_code(self, tenant_id: int, count: int) -> str:
        return f"PROP-{tenant_id}-{count + 1:04d}"


proposal_service = ProposalService()
