from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints, field_validator

LowerSha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
CertificateReference = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"),
]


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    managed_timestamp: Literal[True]
    terms_version: Annotated[str, StringConstraints(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")]
    privacy_version: Annotated[
        str, StringConstraints(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    ]
    accepted_at: AwareDatetime


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    certificate_reference: CertificateReference
    manifest_sha256: LowerSha256
    email: Annotated[str, StringConstraints(min_length=3, max_length=254)]
    consent: ConsentRequest

    @field_validator("email")
    @classmethod
    def validate_syntactic_email(cls, value: str) -> str:
        try:
            return validate_email(value, check_deliverability=False, test_environment=True).normalized
        except EmailNotValidError as error:
            raise ValueError("invalid email") from error


class CheckoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_reference: str
    status_token: str
    checkout_url: str
    payment_state: Literal["checkout_open"]
    fulfillment_state: Literal["awaiting_payment"]


class OrderStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_reference: str
    certificate_reference: str
    manifest_sha256: str
    payment_state: str
    fulfillment_state: str
    created_at: datetime
    updated_at: datetime
    calendar_submitted_at: datetime | None = None
    bitcoin_verified_at: datetime | None = None
    proof_available: bool
    message_code: str | None = None


class RotateTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status_token: str
