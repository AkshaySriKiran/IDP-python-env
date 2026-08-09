from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


Role = Literal["admin", "user"]
UserStatus = Literal["active", "disabled"]


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserPublic"


class UserPublic(BaseModel):
    id: str
    email: str
    role: Role
    status: UserStatus
    display_name: str = ""
    copilot_daily_limit: int = 5
    preferred_model: str
    allowed_models: list[str] = Field(default_factory=list)
    copilot_used_today: int = 0
    copilot_remaining_today: int = 5


class AuthStatusResponse(BaseModel):
    auth_required: bool
    login_enabled: bool = True
    default_copilot_limit: int
    model_catalog: list[str]
    authenticated: bool = False
    user: Optional[UserPublic] = None


class CreateUserRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""
    role: Role = "user"
    copilot_daily_limit: int = 5
    preferred_model: str
    allowed_models: list[str] = Field(default_factory=list)


class UpdateUserRequest(BaseModel):
    display_name: Optional[str] = None
    role: Optional[Role] = None
    status: Optional[UserStatus] = None
    copilot_daily_limit: Optional[int] = None
    preferred_model: Optional[str] = None
    allowed_models: Optional[list[str]] = None
    password: Optional[str] = None


class CopilotRequest(BaseModel):
    question: str
    context: str = ""
    model: Optional[str] = None


class CopilotResponse(BaseModel):
    answer: str
    model: str
    copilot_used_today: int
    copilot_remaining_today: int
    copilot_daily_limit: int


TokenResponse.model_rebuild()
