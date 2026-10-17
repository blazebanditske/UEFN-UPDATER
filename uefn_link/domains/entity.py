"""Scene Graph entities and their components.

The entity toolset is how an agent builds and inspects the level: create
entities, attach components, and read or write component transforms and
properties. Transforms cross the boundary through :mod:`uefn_link.coords`
because the toolset's axis convention differs from the editor's.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from uefn_link import coords
from uefn_link.domains.base import Domain
from uefn_link.mcp.client import extract_structured


class EntityDomain(Domain):
    group = "entity"

    def create(self, class_name: str, *, name: Optional[str] = None,
               timeout: Optional[float] = None) -> str:
        args: Dict[str, Any] = {"class": class_name}
        if name:
            args["name"] = name
        return self.call_text("create", args, timeout=timeout)

    def destroy(self, entity: str, *, timeout: Optional[float] = None) -> str:
        return self.call_text("destroy", {"entity": entity}, timeout=timeout)

    def list_entities(self, *, timeout: Optional[float] = None) -> str:
        """Inventory every entity in the level and its components."""
        return self.call_text("list", {}, timeout=timeout)

    def add_component(self, entity: str, component_class: str, *,
                      timeout: Optional[float] = None) -> str:
        return self.call_text(
            "add_component",
            {"entity": entity, "componentClass": component_class},
            timeout=timeout,
        )

    def list_component_classes(self, *, timeout: Optional[float] = None) -> str:
        return self.call_text("list_component_classes", {}, timeout=timeout)

    # -- transforms -----------------------------------------------------
    def get_transform(self, entity: str, *, component: Optional[str] = None,
                      luf: bool = False, timeout: Optional[float] = None) -> Any:
        """Read a transform as a parsed object.

        Set ``luf=True`` to get left-up-forward output.
        """
        args: Dict[str, Any] = {"entity": entity}
        if component:
            args["component"] = component
        result = extract_structured(self.call("get_transform", args, timeout=timeout))
        return coords.transform_xyz_to_luf(result) if luf and isinstance(result, dict) else result

    def set_transform(self, entity: str, location: Optional[Sequence[float]] = None,
                      rotation: Optional[Sequence[float]] = None,
                      scale: Optional[Sequence[float]] = None,
                      *, component: Optional[str] = None, luf: bool = False,
                      timeout: Optional[float] = None) -> str:
        """Write a transform. ``luf=True`` means the inputs are in LUF axes."""
        loc = list(location) if location is not None else None
        if loc is not None and luf:
            loc = list(coords.luf_to_xyz(loc))
        args: Dict[str, Any] = {"entity": entity}
        if component:
            args["component"] = component
        if loc is not None:
            args["location"] = loc
        if rotation is not None:
            args["rotation"] = list(rotation)
        if scale is not None:
            args["scale"] = list(scale)
        return self.call_text("set_transform", args, timeout=timeout)

    def move(self, entity: str, delta: Sequence[float], *,
             component: Optional[str] = None, timeout: Optional[float] = None) -> str:
        """Offset an entity by ``delta`` in XYZ, reading its current position.

        The toolset has no relative-move tool, so this reads then writes. That
        is two round trips and is therefore not atomic; concurrent edits to the
        same entity can race.
        """
        current = self.get_transform(entity, component=component, timeout=timeout)
        base = _extract_location(current)
        if base is None:
            raise ValueError(
                f"Could not read a location for {entity!r}; got: {current!r}"
            )
        target = [base[i] + float(delta[i]) for i in range(3)]
        return self.set_transform(entity, location=target, component=component,
                                  timeout=timeout)

    # -- properties -----------------------------------------------------
    def get_property(self, entity: str, property_name: str, *,
                     component: Optional[str] = None,
                     timeout: Optional[float] = None) -> Any:
        args: Dict[str, Any] = {"entity": entity, "property": property_name}
        if component:
            args["component"] = component
        return extract_structured(self.call("get_property", args, timeout=timeout))

    def set_property(self, entity: str, property_name: str, value: Any, *,
                     component: Optional[str] = None,
                     timeout: Optional[float] = None) -> str:
        args: Dict[str, Any] = {"entity": entity, "property": property_name, "value": value}
        if component:
            args["component"] = component
        return self.call_text("set_property", args, timeout=timeout)

    def describe_operations(self) -> List[Dict[str, Any]]:
        return [
            self.describe(op)
            for op in ("create", "destroy", "list", "add_component",
                       "list_component_classes", "get_transform", "set_transform")
        ]


def _extract_location(transform: Any) -> Optional[List[float]]:
    if not isinstance(transform, dict):
        return None
    for key in ("location", "Location", "translation", "Translation", "position", "Position"):
        value = transform.get(key)
        if isinstance(value, (list, tuple)) and len(value) == 3:
            return [float(v) for v in value]
    # Some responses nest the payload one level down.
    for nested in transform.values():
        if isinstance(nested, dict):
            found = _extract_location(nested)
            if found is not None:
                return found
    return None
