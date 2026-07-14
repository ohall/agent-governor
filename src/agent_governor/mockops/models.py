"""Domain models for the mock ops estate."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IncidentState(StrEnum):
    NEW = "new"
    INVESTIGATING = "investigating"
    REMEDIATING = "remediating"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ServiceTier(StrEnum):
    """Business criticality of a CMDB service. TIER_1 is customer-facing revenue path."""

    TIER_1 = "tier-1"
    TIER_2 = "tier-2"
    TIER_3 = "tier-3"


class Incident(BaseModel):
    id: str
    title: str
    description: str
    severity: Severity
    state: IncidentState
    service_id: str
    assigned_to: str | None = None
    resolution_note: str | None = None
    created_at: datetime
    updated_at: datetime


class IncidentUpdate(BaseModel):
    """Partial update; only provided fields are applied."""

    state: IncidentState | None = None
    severity: Severity | None = None
    assigned_to: str | None = None
    description: str | None = None


class ResolveRequest(BaseModel):
    resolution_note: str = Field(min_length=1)


class Service(BaseModel):
    id: str
    name: str
    owner: str
    tier: ServiceTier
    dependencies: list[str] = Field(default_factory=list)
    healthy: bool = True
    version: str


class User(BaseModel):
    id: str
    name: str
    email: str
    roles: list[str] = Field(default_factory=list)


class PrivilegeChange(BaseModel):
    role: str = Field(min_length=1)
    action: str = Field(pattern="^(grant|revoke)$")


class DeployRequest(BaseModel):
    service_id: str
    version: str = Field(min_length=1)


class DeployResult(BaseModel):
    service_id: str
    previous_version: str
    deployed_version: str
    deployed_at: datetime


class NotifyRequest(BaseModel):
    channel: str = Field(pattern="^(email|slack|pager)$")
    recipient: str = Field(min_length=1)
    message: str = Field(min_length=1)


class NotifyResult(BaseModel):
    notification_id: str
    channel: str
    recipient: str
    queued_at: datetime


class RecordSet(BaseModel):
    name: str
    description: str
    record_count: int
    contains_pii: bool


class DeletionResult(BaseModel):
    record_set: str
    deleted_count: int
    deleted_at: datetime
