"""Request models. Strict integers are important: 12.5 ms or true must not
be silently coerced into an integer."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, StrictInt, StringConstraints, model_validator

NonNegInt = Annotated[int, Field(strict=True, ge=0)]
TargetInt = Annotated[int, Field(strict=True, ge=0, le=255)]
NameStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                           max_length=100)]
CueId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                         max_length=64)]


class AbsoluteStart(BaseModel):
    mode: Literal["absolute"]
    at: NonNegInt


class ReferenceStart(BaseModel):
    mode: Literal["reference"]
    refId: CueId
    anchor: Literal["start", "end"] = "end"
    offset: NonNegInt = 0


StartSpec = Annotated[Union[AbsoluteStart, ReferenceStart],
                      Field(discriminator="mode")]


class CueInput(BaseModel):
    id: CueId
    fixture: Annotated[str, StringConstraints(strip_whitespace=True,
                                              min_length=1, max_length=100)]
    channel: Annotated[str, StringConstraints(strip_whitespace=True,
                                              min_length=1, max_length=100)]
    target: TargetInt
    duration: NonNegInt
    start: StartSpec

    @model_validator(mode="after")
    def _no_self_reference(self) -> "CueInput":
        st = self.start
        if getattr(st, "mode", None) == "reference" and st.refId == self.id:
            raise ValueError("起点不能引用自身")
        return self


class ProjectPayload(BaseModel):
    name: NameStr
    cues: list[CueInput]


class CreateProject(BaseModel):
    name: NameStr = "未命名工程"
    cues: list[CueInput] = []
