"""Capability wrappers over the editor's advertised MCP toolsets."""
from uefn_link.domains.base import Domain
from uefn_link.domains.device import DeviceDomain
from uefn_link.domains.entity import EntityDomain
from uefn_link.domains.project import ProjectDomain
from uefn_link.domains.python_assets import PythonDomain
from uefn_link.domains.session import SessionDomain
from uefn_link.domains.verse import VerseDomain

__all__ = [
    "Domain",
    "DeviceDomain",
    "EntityDomain",
    "ProjectDomain",
    "PythonDomain",
    "SessionDomain",
    "VerseDomain",
]
