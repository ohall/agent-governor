from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_governor.warden.approvals import ApprovalQueue, ApprovalStatus


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 14, 9, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def queue(clock: FakeClock) -> ApprovalQueue:
    return ApprovalQueue(":memory:", clock=clock)


def submit(queue: ApprovalQueue, timeout_s: int = 1800) -> str:
    request = queue.submit(
        agent_id="agent-x",
        owner="maria.chen",
        tool="delete_records",
        params={"record_set": "customer_pii_archive"},
        action_class="always_approve",
        category="data_deletion",
        risk="high",
        timeout_s=timeout_s,
    )
    return request.id


class TestQueueLifecycle:
    def test_submit_creates_pending(self, queue: ApprovalQueue) -> None:
        approval_id = submit(queue)
        request = queue.get(approval_id)
        assert request is not None
        assert request.status is ApprovalStatus.PENDING
        assert request.category == "data_deletion"
        assert request.params == {"record_set": "customer_pii_archive"}

    def test_approve_records_who_and_when(self, queue: ApprovalQueue, clock: FakeClock) -> None:
        approval_id = submit(queue)
        clock.advance(60)
        decided = queue.decide(approval_id, approve=True, decided_by="maria.chen")
        assert decided.status is ApprovalStatus.APPROVED
        assert decided.decided_by == "maria.chen"
        assert decided.decided_at == clock.now

    def test_deny_records_reason(self, queue: ApprovalQueue) -> None:
        approval_id = submit(queue)
        decided = queue.decide(
            approval_id, approve=False, decided_by="priya.nair", reason="legal hold"
        )
        assert decided.status is ApprovalStatus.DENIED
        assert decided.reason == "legal hold"

    def test_double_decision_rejected(self, queue: ApprovalQueue) -> None:
        approval_id = submit(queue)
        queue.decide(approval_id, approve=True, decided_by="maria.chen")
        with pytest.raises(ValueError, match="already approved"):
            queue.decide(approval_id, approve=False, decided_by="eve")

    def test_unknown_id_raises(self, queue: ApprovalQueue) -> None:
        with pytest.raises(KeyError):
            queue.decide("APR-nope", approve=True, decided_by="x")


class TestTimeout:
    def test_pending_expires_after_timeout(self, queue: ApprovalQueue, clock: FakeClock) -> None:
        approval_id = submit(queue, timeout_s=1800)
        clock.advance(1799)
        request = queue.get(approval_id)
        assert request is not None
        assert request.status is ApprovalStatus.PENDING
        clock.advance(2)
        request = queue.get(approval_id)
        assert request is not None
        assert request.status is ApprovalStatus.EXPIRED
        assert request.decided_by == "system"
        assert "auto-denied" in (request.reason or "")

    def test_expired_cannot_be_approved(self, queue: ApprovalQueue, clock: FakeClock) -> None:
        approval_id = submit(queue, timeout_s=60)
        clock.advance(61)
        with pytest.raises(ValueError, match="already expired"):
            queue.decide(approval_id, approve=True, decided_by="late.human")

    def test_list_filters_by_status(self, queue: ApprovalQueue, clock: FakeClock) -> None:
        first = submit(queue, timeout_s=60)
        clock.advance(61)  # first expires
        second = submit(queue, timeout_s=600)
        pending = queue.list(ApprovalStatus.PENDING)
        expired = queue.list(ApprovalStatus.EXPIRED)
        assert [r.id for r in pending] == [second]
        assert [r.id for r in expired] == [first]


class TestCrossProcessPersistence:
    def test_two_queue_instances_share_a_file(self, tmp_path: Path) -> None:
        db = str(tmp_path / "approvals.sqlite3")
        gateway_side = ApprovalQueue(db)
        approval_id = submit(gateway_side)

        cli_side = ApprovalQueue(db)  # simulates `warden approvals` in another process
        cli_side.decide(approval_id, approve=True, decided_by="maria.chen")

        seen = gateway_side.get(approval_id)
        assert seen is not None
        assert seen.status is ApprovalStatus.APPROVED
        assert seen.decided_by == "maria.chen"
