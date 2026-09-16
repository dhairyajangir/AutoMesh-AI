from typing import Literal

from pydantic import BaseModel, Field, model_validator

ViewName = Literal["side", "front", "rear", "top"]


class ProjectConfig(BaseModel):
    resolution: Literal[128, 256, 384] = 256
    smoothing: bool = False
    known_axis: Literal["length", "width", "height"] = "length"
    known_dimension: float | None = Field(default=None, gt=0, le=100000)
    unit: Literal["m", "mm", "cm"] = "m"


class ProjectCreate(BaseModel):
    name: str = Field(default="Untitled vehicle", min_length=1, max_length=100)
    mode: Literal["blueprint", "photo"] = "blueprint"


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    config: ProjectConfig | None = None


class ViewSettings(BaseModel):
    crop: tuple[float, float, float, float] = (0, 0, 1, 1)
    rotation: Literal[0, 90, 180, 270] = 0
    flip_x: bool = False
    threshold: int = Field(default=160, ge=0, le=255)
    invert: bool = False
    scale: float = Field(default=1, ge=0.25, le=3)
    offset_x: float = Field(default=0, ge=-1, le=1)
    offset_y: float = Field(default=0, ge=-1, le=1)
    orientation_confirmed: bool = False

    @model_validator(mode="after")
    def check_crop(self):
        x, y, w, h = self.crop
        if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0):
            raise ValueError("Crop must have positive width and height inside the image.")
        if x + w > 1.000001 or y + h > 1.000001:
            raise ValueError("Crop extends beyond the source image.")
        return self


class JobRequest(BaseModel):
    kind: Literal["segment", "reconstruct"]
    view: ViewName | None = None
    resolution: Literal[128, 256, 384] | None = None


class ArtifactInfo(BaseModel):
    id: str
    name: str
    kind: str
    mime: str


class ViewInfo(BaseModel):
    id: str
    name: ViewName
    source_id: str
    mask_id: str | None
    image_id: str | None
    settings: ViewSettings
    revision: int


class ProjectInfo(BaseModel):
    id: str
    name: str
    mode: Literal["blueprint", "photo"]
    config: ProjectConfig
    revision: int
    created: str
    updated: str
    last_job_id: str | None
    thumbnail_id: str | None = None
    views: list[ViewInfo]
    result: dict | None = None
    stale: bool = False


class JobInfo(BaseModel):
    id: str
    project_id: str
    kind: str
    state: str
    stage: str
    created: str
    updated: str
    error: str | None
    result: dict | None
    revision: int
