from datetime import datetime
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _normalize_company_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\s\-‐‑–—−·・.．,，、:：;；()（）\[\]【】]+", "", normalized)


def _normalize_tax_id(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).upper()


class CompanyIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=255)
    tax_id: str = Field(min_length=1, max_length=50)

    @field_validator("company_name")
    @classmethod
    def clean_company_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("公司名称不能为空")
        return cleaned

    @field_validator("tax_id")
    @classmethod
    def clean_tax_id(cls, value: str) -> str:
        cleaned = _normalize_tax_id(value)
        if not cleaned:
            raise ValueError("纳税人识别号不能为空")
        return cleaned


class CompanyIdentityResponse(BaseModel):
    company_name: str
    tax_id: str


class CompanyProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identities: list[CompanyIdentity] = Field(min_length=1)

    @model_validator(mode="after")
    def ensure_unique_identities(self) -> "CompanyProfileUpdate":
        names: set[str] = set()
        tax_ids: set[str] = set()
        for identity in self.identities:
            normalized_name = _normalize_company_name(identity.company_name)
            if normalized_name in names:
                raise ValueError("公司名称不能重复")
            if identity.tax_id in tax_ids:
                raise ValueError("纳税人识别号不能重复")
            names.add(normalized_name)
            tax_ids.add(identity.tax_id)
        return self


class CompanyProfileResponse(BaseModel):
    version: int
    identities: list[CompanyIdentityResponse]
    updated_at: datetime
