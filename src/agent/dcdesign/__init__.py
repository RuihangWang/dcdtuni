"""Compact design language for the hybrid air–liquid data-hall design task.

``spec``      the cooling-architecture families, the task (requirement) and the design
``families``  builds each family's template project from the base Shinaide_liquid project
``compiler``  task + design -> a complete ``.cfdproj`` (family template + controls)

Version 1 keeps the base project's racks, servers, CDU, UPS and liquid network
fixed for every family, so the families differ only on the air side (cooling
units, supply path and containment) and the liquid network is identical across
designs. Row orientation and arrangement enter in a later version together with
automatic re-routing of the liquid rings.
"""
