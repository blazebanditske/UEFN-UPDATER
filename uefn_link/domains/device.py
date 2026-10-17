"""Creative devices: browse the catalog, place devices, edit @editable
properties, and wire device event bindings.

Devices are how Fortnite-specific behaviour is configured. Anything an agent
writes here lands as a real device the creator can select and tune, so the
operations mirror the Details panel rather than inventing a parallel model.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from uefn_link.domains.base import Domain
from uefn_link.mcp.client import extract_structured


class DeviceDomain(Domain):
    group = "device"

    # -- catalog --------------------------------------------------------
    def list_assets(self, *, filter_text: Optional[str] = None,
                    timeout: Optional[float] = None) -> str:
        args: Dict[str, Any] = {}
        if filter_text:
            args["filter"] = filter_text
        return self.call_text("list_assets", args, timeout=timeout)

    def place(self, asset: str, *, location: Optional[Sequence[float]] = None,
              rotation: Optional[Sequence[float]] = None,
              timeout: Optional[float] = None) -> str:
        args: Dict[str, Any] = {"asset": asset}
        if location is not None:
            args["location"] = list(location)
        if rotation is not None:
            args["rotation"] = list(rotation)
        return self.call_text("place", args, timeout=timeout)

    # -- properties -----------------------------------------------------
    def get_properties(self, device: str, *, timeout: Optional[float] = None) -> Any:
        """Read a device's properties as a parsed object."""
        return extract_structured(self.call("get_properties", {"device": device},
                                            timeout=timeout))

    def set_property(self, device: str, property_name: str, value: Any, *,
                     timeout: Optional[float] = None) -> str:
        """Set a single @editable property.

        Only properties visible in the UEFN UI are allow-listed; anything else
        passes here and fails project validation, which is why
        :meth:`verify_property` exists.
        """
        return self.call_text(
            "set_property",
            {"device": device, "property": property_name, "value": value},
            timeout=timeout,
        )

    def configure(self, device: str, properties: Dict[str, Any], *,
                  timeout: Optional[float] = None) -> Dict[str, str]:
        """Set several properties, reporting per-property results.

        Failures are collected rather than raised so one bad property does not
        hide the rest, and the caller can see exactly which one was rejected.
        """
        results: Dict[str, str] = {}
        for key, value in properties.items():
            try:
                results[key] = self.set_property(device, key, value, timeout=timeout)
            except Exception as exc:  # reported, not swallowed
                results[key] = f"FAILED: {exc}"
        return results

    def verify_property(self, device: str, property_name: str, expected: Any,
                        *, timeout: Optional[float] = None) -> bool:
        """Read a property back and compare it to ``expected``.

        The editor can accept a write and still not apply it, so a read-back is
        the only reliable confirmation.
        """
        actual = self.get_properties(device, timeout=timeout)
        return _contains_value(actual, property_name, expected)

    # -- event bindings -------------------------------------------------
    def get_binding_options(self, device: str, *, timeout: Optional[float] = None) -> str:
        return self.call_text("get_binding_options", {"device": device}, timeout=timeout)

    def list_event_bindings(self, device: str, *, timeout: Optional[float] = None) -> str:
        return self.call_text("list_event_bindings", {"device": device}, timeout=timeout)

    def add_event_binding(self, device: str, event: str, function: str, *,
                          target: Optional[str] = None,
                          timeout: Optional[float] = None) -> str:
        args: Dict[str, Any] = {"device": device, "event": event, "function": function}
        if target:
            args["target"] = target
        return self.call_text("add_event_binding", args, timeout=timeout)

    def remove_event_binding(self, device: str, event: str, *,
                             timeout: Optional[float] = None) -> str:
        return self.call_text(
            "remove_event_binding", {"device": device, "event": event}, timeout=timeout
        )

    def describe_operations(self) -> List[Dict[str, Any]]:
        return [
            self.describe(op)
            for op in ("list_assets", "place", "get_properties", "set_property",
                       "add_event_binding", "list_event_bindings")
        ]


def _contains_value(haystack: Any, name: str, expected: Any) -> bool:
    """Search a properties payload for ``name`` holding ``expected``."""
    if isinstance(haystack, str):
        return _loose_equal(name, expected, haystack)
    if isinstance(haystack, dict):
        for key, value in haystack.items():
            if str(key).lower() == name.lower() and _loose_equal_value(value, expected):
                return True
            if isinstance(value, (dict, list, str)) and _contains_value(value, name, expected):
                return True
        return False
    if isinstance(haystack, list):
        return any(_contains_value(item, name, expected) for item in haystack)
    return False


def _loose_equal(name: str, expected: Any, blob: str) -> bool:
    low = blob.lower()
    return name.lower() in low and str(expected).lower() in low


def _loose_equal_value(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.strip().lower() == expected.strip().lower()
    try:
        return float(actual) == float(expected)
    except (TypeError, ValueError):
        return actual == expected
