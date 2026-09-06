from pydantic import BaseModel, Field


class ResolveRequest(BaseModel):
    query: str = Field(min_length=1, description="Vehicle text or identifier to resolve.")


class ResolveCandidate(BaseModel):
    vehicle_id: str
    label: str
    confidence: float = Field(ge=0.0, le=1.0)


class ResolveResponse(BaseModel):
    query: str
    status: str
    candidates: list[ResolveCandidate]


class ResolveStatusResponse(BaseModel):
    status: str
    feature: str

