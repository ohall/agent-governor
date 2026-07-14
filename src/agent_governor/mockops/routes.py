"""HTTP routes for the mock ops estate."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agent_governor.mockops.faults import FaultConfig, FaultInjector
from agent_governor.mockops.models import (
    DeletionResult,
    DeployRequest,
    DeployResult,
    Incident,
    IncidentState,
    IncidentUpdate,
    NotifyRequest,
    NotifyResult,
    PrivilegeChange,
    RecordSet,
    ResolveRequest,
    Service,
    User,
)
from agent_governor.mockops.state import Store, build_store

if TYPE_CHECKING:
    from fastapi import FastAPI

router = APIRouter()
admin_router = APIRouter(prefix="/admin", tags=["admin"])


def _store(request: Request) -> Store:
    store: Store = request.app.state.store
    return store


def _now() -> datetime:
    return datetime.now(UTC)


# --- incidents ----------------------------------------------------------


@router.get("/incidents", tags=["incidents"])
def list_incidents(
    request: Request,
    state: Annotated[IncidentState | None, None] = None,
    service_id: str | None = None,
) -> list[Incident]:
    incidents = list(_store(request).incidents.values())
    if state is not None:
        incidents = [i for i in incidents if i.state == state]
    if service_id is not None:
        incidents = [i for i in incidents if i.service_id == service_id]
    return sorted(incidents, key=lambda i: i.created_at)


@router.get("/incidents/{incident_id}", tags=["incidents"])
def get_incident(request: Request, incident_id: str) -> Incident:
    incident = _store(request).incidents.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
    return incident


@router.patch("/incidents/{incident_id}", tags=["incidents"])
def update_incident(request: Request, incident_id: str, update: IncidentUpdate) -> Incident:
    incident = get_incident(request, incident_id)
    changes = update.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=422, detail="no fields to update")
    updated = incident.model_copy(update={**changes, "updated_at": _now()})
    _store(request).incidents[incident_id] = updated
    return updated


@router.post("/incidents/{incident_id}/resolve", tags=["incidents"])
def resolve_incident(request: Request, incident_id: str, body: ResolveRequest) -> Incident:
    incident = get_incident(request, incident_id)
    if incident.state in {IncidentState.RESOLVED, IncidentState.CLOSED}:
        raise HTTPException(status_code=409, detail=f"incident already {incident.state}")
    updated = incident.model_copy(
        update={
            "state": IncidentState.RESOLVED,
            "resolution_note": body.resolution_note,
            "updated_at": _now(),
        }
    )
    _store(request).incidents[incident_id] = updated
    return updated


# --- CMDB ---------------------------------------------------------------


@router.get("/cmdb/services", tags=["cmdb"])
def list_services(request: Request) -> list[Service]:
    return sorted(_store(request).services.values(), key=lambda s: s.id)


@router.get("/cmdb/services/{service_id}", tags=["cmdb"])
def get_service(request: Request, service_id: str) -> Service:
    service = _store(request).services.get(service_id)
    if service is None:
        raise HTTPException(status_code=404, detail=f"service {service_id} not found")
    return service


# --- risky operations ----------------------------------------------------


@router.post("/deploy", tags=["ops"])
def deploy(request: Request, body: DeployRequest) -> DeployResult:
    store = _store(request)
    service = store.services.get(body.service_id)
    if service is None:
        raise HTTPException(status_code=404, detail=f"service {body.service_id} not found")
    previous = service.version
    store.services[service.id] = service.model_copy(update={"version": body.version})
    store.deploys_performed += 1
    return DeployResult(
        service_id=service.id,
        previous_version=previous,
        deployed_version=body.version,
        deployed_at=_now(),
    )


@router.post("/notify", tags=["ops"])
def notify(request: Request, body: NotifyRequest) -> NotifyResult:
    store = _store(request)
    store.notifications_sent += 1
    return NotifyResult(
        notification_id=f"NTF-{uuid.uuid4().hex[:8]}",
        channel=body.channel,
        recipient=body.recipient,
        queued_at=_now(),
    )


@router.post("/users/{user_id}/privileges", tags=["ops"])
def change_privileges(request: Request, user_id: str, body: PrivilegeChange) -> User:
    store = _store(request)
    user = store.users.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"user {user_id} not found")
    roles = set(user.roles)
    if body.action == "grant":
        roles.add(body.role)
    else:
        if body.role not in roles:
            raise HTTPException(status_code=409, detail=f"user lacks role {body.role!r}")
        roles.discard(body.role)
    updated = user.model_copy(update={"roles": sorted(roles)})
    store.users[user_id] = updated
    return updated


@router.get("/records", tags=["ops"])
def list_record_sets(request: Request) -> list[RecordSet]:
    return sorted(_store(request).record_sets.values(), key=lambda r: r.name)


@router.delete("/records/{record_set}", tags=["ops"])
def delete_records(request: Request, record_set: str) -> DeletionResult:
    store = _store(request)
    records = store.record_sets.get(record_set)
    if records is None:
        raise HTTPException(status_code=404, detail=f"record set {record_set!r} not found")
    deleted = records.record_count
    store.record_sets[record_set] = records.model_copy(update={"record_count": 0})
    return DeletionResult(record_set=record_set, deleted_count=deleted, deleted_at=_now())


# --- admin (exempt from fault injection) ---------------------------------


class HealthResponse(BaseModel):
    status: str
    seed: int


class ResetResponse(BaseModel):
    status: str
    seed: int


@admin_router.get("/faults")
def get_fault_config(request: Request) -> FaultConfig:
    injector: FaultInjector = request.app.state.faults
    return injector.config


@admin_router.put("/faults")
def set_fault_config(request: Request, config: FaultConfig) -> FaultConfig:
    request.app.state.faults = FaultInjector(config)
    return config


@admin_router.post("/reset")
def reset(request: Request) -> ResetResponse:
    app: FastAPI = request.app
    seed: int = app.state.settings.seed
    app.state.store = build_store(seed)
    app.state.faults = FaultInjector(app.state.faults.config.model_copy())
    return ResetResponse(status="reset", seed=seed)
