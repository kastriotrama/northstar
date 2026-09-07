from api.app.features.resolve.schemas import (
    ResolveCandidate,
    ResolveResponse,
    ResolveStatusResponse,
)


class ResolveService:
    def get_status(self) -> ResolveStatusResponse:
        return ResolveStatusResponse(status="stubbed", feature="vehicle-resolution")

    def resolve(self, query: str) -> ResolveResponse:
        return ResolveResponse(
            query=query,
            status="stubbed",
            candidates=self._get_stub_candidates(),
        )

    def _get_stub_candidates(self) -> list[ResolveCandidate]:
        return []
