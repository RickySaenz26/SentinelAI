from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()


class RecoveryRequest(StrictModel):
    email: EmailStr


class RecoveryConfirmRequest(StrictModel):
    token: str = Field(min_length=32, max_length=512)
    new_password: str = Field(min_length=12, max_length=1024)


class ActiveOrganizationRequest(StrictModel):
    organization_id: UUID


class OrganizationPatchRequest(StrictModel):
    name: str = Field(min_length=2, max_length=160)


class MembershipCreateRequest(StrictModel):
    email: EmailStr
    role_code: str = Field(pattern=r"^[a-z_]{3,64}$")


class MembershipPatchRequest(StrictModel):
    role_code: str | None = Field(default=None, pattern=r"^[a-z_]{3,64}$")
    status: str | None = Field(default=None, pattern=r"^(active|suspended)$")


class CursorPage(StrictModel):
    cursor: str | None = Field(default=None, max_length=128)
    limit: int = Field(default=50, ge=1, le=100)
