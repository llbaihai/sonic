from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Data(BaseModel, Generic[T]):
    data: T


class Page(BaseModel, Generic[T]):
    data: list[T]
    next_cursor: UUID | None = None


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Register(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9_]+$")
    password: str = Field(min_length=12, max_length=128)


class Login(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    username: str
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=120)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    created_at: datetime


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict] = Field(default_factory=list)
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    codec: str
    duration_ms: int
    sample_rate: int
    channels: int
    created_at: datetime


class Clip(Input):
    asset_id: UUID
    start_ms: int = Field(ge=0, le=3600000)
    offset_ms: int = Field(ge=0, le=600000)
    duration_ms: int = Field(gt=0, le=600000)
    gain_db: float = Field(default=0, ge=-60, le=12, allow_inf_nan=False)


class Track(Input):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    clips: list[Clip] = Field(max_length=128)


class DraftInput(Input):
    expected_version: int = Field(ge=0, le=2147483646)
    tracks: list[Track] = Field(max_length=32)


class DraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    project_id: UUID
    version: int
    tracks: list[Track]
    updated_at: datetime


class WorkInput(Input):
    title: str = Field(min_length=1, max_length=120)
    asset_id: UUID


class WorkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    asset_id: UUID
    title: str
    created_at: datetime


class PasswordChange(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    current_password: str = Field(min_length=1,max_length=128)
    new_password: str = Field(min_length=12,max_length=128)
