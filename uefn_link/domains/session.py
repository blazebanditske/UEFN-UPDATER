"""Play sessions: launch, inspect, push changes, and stop.

UEFN plays in client (PIC) rather than in editor (PIE), which changes what a
session means: there is a separate client process to wait on, and a running
session must be relaunched before newly added assets (textures, meshes) are
visible. :meth:`SessionDomain.live_edit_cycle` encodes that ordering so callers
do not rediscover it.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from uefn_link.domains.base import Domain

# Starting a session and pushing changes are genuinely slow: the editor has to
# build, cook, and hand a client its payload.
START_TIMEOUT = 600.0
PUSH_TIMEOUT = 600.0
STOP_TIMEOUT = 180.0


class SessionDomain(Domain):
    group = "session"

    def start(self, *, timeout: Optional[float] = START_TIMEOUT) -> str:
        """Launch a play session."""
        return self.call_text("start", {}, timeout=timeout)

    def start_game(self, *, timeout: Optional[float] = STOP_TIMEOUT) -> str:
        return self.call_text("start_game", {}, timeout=timeout)

    def stop(self, *, timeout: Optional[float] = STOP_TIMEOUT) -> str:
        return self.call_text("stop", {}, timeout=timeout)

    def stop_game(self, *, timeout: Optional[float] = 120.0) -> str:
        return self.call_text("stop_game", {}, timeout=timeout)

    def status(self, *, timeout: Optional[float] = None) -> str:
        return self.call_text("status", {}, timeout=timeout)

    def is_running(self, *, timeout: Optional[float] = None) -> bool:
        return _looks_running(self.status(timeout=timeout))

    def push_changes(self, *, timeout: Optional[float] = PUSH_TIMEOUT) -> str:
        """Push Verse changes into a session that is already running.

        This cannot deliver *new assets*. A texture or mesh added since the
        session started needs a relaunch; see :meth:`live_edit_cycle`.
        """
        return self.call_text("push_changes", {}, timeout=timeout)

    def game_state(self, *, timeout: Optional[float] = None) -> str:
        return self.call_text("game_state", {}, timeout=timeout)

    def client_log(self, *, timeout: Optional[float] = None) -> str:
        """Read the client log, where runtime errors and prints appear."""
        return self.call_text("client_log", {}, timeout=timeout)

    # -- composites -----------------------------------------------------
    def wait_for_running(self, *, timeout: float = 300.0, interval: float = 5.0,
                         sleep: Callable[[float], None] = time.sleep) -> bool:
        """Poll until the session reports running, or ``timeout`` elapses."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_running():
                return True
            sleep(interval)
        return False

    def live_edit_cycle(self, *, restart: bool = False,
                        settle: float = 5.0,
                        sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
        """Bring a session to the state that reflects the current project.

        ``restart=True`` stops any session and starts a fresh one, which is
        required after adding new assets. ``restart=False`` pushes Verse
        changes into the running session, which is faster but cannot deliver
        new assets.
        """
        steps: List[Dict[str, Any]] = []
        if restart:
            if self.is_running():
                steps.append({"stop": self.stop()})
            steps.append({"start": self.start()})
        else:
            steps.append({"push_changes": self.push_changes()})
        sleep(settle)
        steps.append({"status": self.status()})
        # Diagnostics: a build without the game-state tool should still cycle.
        state = self.try_call_text("game_state")
        if state is not None:
            steps.append({"game_state": state})
        return {"restart": restart, "steps": steps}

    def describe_operations(self) -> List[Dict[str, Any]]:
        return [
            self.describe(op)
            for op in ("start", "stop", "status", "push_changes",
                       "game_state", "client_log")
        ]


def _looks_running(status_text: str) -> bool:
    low = (status_text or "").lower()
    if any(word in low for word in ("not running", "no session", "stopped", "inactive", "disconnected")):
        return False
    return any(word in low for word in ("running", "active", "connected", "in progress", "live"))
