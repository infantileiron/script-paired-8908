from pydantic import BaseModel, Field
from typing import Optional


class SamplingGroupInput(BaseModel):
    team: str = Field(min_length=1, max_length=40)
    roster: list[str] = Field(default_factory=list)
    sample_size: int


class SamplingPlanInput(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    seed: int = Field(ge=0, le=2147483647)
    groups: list[SamplingGroupInput] = Field(min_length=2, max_length=2)


class ObservationInput(BaseModel):
    before: Optional[float] = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    after: Optional[float] = Field(default=None, ge=0, le=100, allow_inf_nan=False)


class ObservationWithCode(ObservationInput):
    code: str = Field(min_length=1, max_length=60)
