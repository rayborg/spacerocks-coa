from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

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


class CheckoutPriceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_minor: Annotated[int, Field(ge=1, le=100_000_000)]
    currency: Literal["usd"]


class MetbullRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: Annotated[int, Field(ge=1, le=999_999_999)]
    official_url: Annotated[
        str,
        StringConstraints(pattern=r"^https://www\.lpi\.usra\.edu/meteor/metbull\.cfm\?code=[1-9][0-9]{0,8}$"),
    ]
    canonical_name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    record_status: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    official_name: Literal[True]
    recommended_classification: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    fall_or_find: Literal["Fall", "Find"]
    year_found: Annotated[int, Field(ge=1, le=9999)] | None = None
    country: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    latitude: Annotated[
        str, StringConstraints(pattern=r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$", max_length=32)
    ] | None = None
    longitude: Annotated[
        str, StringConstraints(pattern=r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$", max_length=32)
    ] | None = None

    @model_validator(mode="after")
    def validate_official_url_code(self) -> MetbullRecordResponse:
        if self.official_url != f"https://www.lpi.usra.edu/meteor/metbull.cfm?code={self.code}":
            raise ValueError("official URL does not match code")
        return self


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
