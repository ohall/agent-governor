"""Tool execution with transient-fault retry, on top of the gateway."""

from __future__ import annotations

from dataclasses import dataclass

from agent_governor.warden.gateway import Gateway, GatewaySession, ToolCall, ToolResult

TRANSIENT_STATUSES = frozenset({429, 504})


@dataclass(frozen=True)
class ExecResult:
    result: ToolResult
    retries: int

    @property
    def recovered(self) -> bool:
        """True when a transient fault was retried into a success."""
        return self.retries > 0 and self.result.ok


class ToolExecutor:
    """Executes tool calls through the gateway, retrying transient faults and
    transparently refreshing expired short-lived tokens (once per call)."""

    def __init__(
        self,
        gateway: Gateway,
        session: GatewaySession,
        max_retries: int = 2,
        auto_refresh: bool = True,
    ) -> None:
        self._gateway = gateway
        self._session = session
        self._max_retries = max_retries
        self._auto_refresh = auto_refresh

    @property
    def session(self) -> GatewaySession:
        return self._session

    def execute(self, call: ToolCall) -> ExecResult:
        retries = 0
        result = self._gateway.execute(self._session, call)
        if self._auto_refresh and result.denied and "token expired" in (result.error or ""):
            self._session = self._gateway.refresh(self._session.manifest.agent_id)
            result = self._gateway.execute(self._session, call)
        while result.status_code in TRANSIENT_STATUSES and retries < self._max_retries:
            retries += 1
            result = self._gateway.execute(self._session, call)
        return ExecResult(result=result, retries=retries)
