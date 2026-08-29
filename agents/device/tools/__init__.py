"""Device-domain ADK function tools (SPEC §10)."""

from .device import (
    check_inventory,
    get_delivery_status,
    get_device_assignment,
    get_employee_device_requirements,
    request_replacement,
    reserve_device,
)

__all__ = [
    "check_inventory",
    "get_delivery_status",
    "get_device_assignment",
    "get_employee_device_requirements",
    "request_replacement",
    "reserve_device",
]
