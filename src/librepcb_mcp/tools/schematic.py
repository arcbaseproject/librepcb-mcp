from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..librepcb import circuit_ops as co
from ..librepcb import inspect
from ..service import Service
from . import ComponentRef, ProjectPath, Schematic, registrar

PinRef = Annotated[str, Field(description="Pin as 'DESIGNATOR:PIN', PIN = symbol pin name or "
                                          "component signal name, e.g. 'R1:1', 'U1:VCC'")]


def register(mcp, svc: Service) -> None:  # noqa: ANN001
    tool = registrar(mcp)

    # ---- inspection -------------------------------------------------------------
    @tool(read_only=True)
    def list_schematics(project_path: ProjectPath) -> dict:
        """List schematic pages (id, name, symbol and net segment counts)."""
        return {"schematics": inspect.list_schematics(svc.open(project_path))}

    @tool(read_only=True)
    def list_components(project_path: ProjectPath) -> dict:
        """List component instances: designator, value, library component, placement status."""
        prj = svc.open(project_path)
        return {"components": [inspect.component(prj, c) for c in prj.components()]}

    @tool(read_only=True)
    def get_component(project_path: ProjectPath, component: ComponentRef) -> dict:
        """Full details of one component: attributes, signal->net mapping, schematic
        symbols with pin positions, board devices with pad positions/nets."""
        prj = svc.open(project_path)
        return inspect.component(prj, prj.component(component), detailed=True)

    @tool(read_only=True)
    def list_nets(project_path: ProjectPath) -> dict:
        """List nets with their connected component signals and wiring statistics."""
        return {"nets": inspect.list_nets(svc.open(project_path))}

    @tool(read_only=True)
    def get_net_connections(project_path: ProjectPath,
                            net: Annotated[str, Field(description="Net name or UUID")]) -> dict:
        """Connections of one net: component signals, schematic segments/labels, traces, vias."""
        prj = svc.open(project_path)
        return inspect.net_connections(prj, prj.net(net))

    @tool(read_only=True)
    def inspect_schematic(project_path: ProjectPath, schematic: Schematic = None) -> dict:
        """Symbols with positions, and every wire (pin/junction to pin/junction) per net segment."""
        prj = svc.open(project_path)
        return inspect.schematic(prj, prj.schematic(schematic)[0])

    # ---- editing ------------------------------------------------------------------
    @tool()
    def create_schematic(project_path: ProjectPath,
                         name: Annotated[str, Field(description="Schematic page name")]) -> dict:
        """Add an empty schematic page."""
        return svc.edit(project_path, "create_schematic", lambda p: co.create_schematic(p, name))

    @tool()
    def add_component(
        project_path: ProjectPath,
        device: Annotated[str, Field(description="Library device UUID (preferred) or exact device "
                                                 "name, from search_components")],
        x_mm: float, y_mm: float,
        rotation_deg: Annotated[float, Field(description="Counter-clockwise")] = 0.0,
        schematic: Schematic = None,
        name: Annotated[str | None, Field(description="Designator; default: prefix + next number")] = None,
        value: Annotated[str | None, Field(description="Value text; default: library default "
                                                       "(may contain {{ATTRIBUTE}} placeholders)")] = None,
    ) -> dict:
        """Add a component from the installed libraries to the circuit and place all its
        symbols (gates) on a schematic. Library elements are copied into the project
        library. Use add_device_to_board to put it on a PCB. Schematic grid is 2.54 mm."""
        return svc.edit(project_path, "add_component", lambda p: co.add_component(
            p, svc.lib, device, x_mm, y_mm, rotation_deg, schematic, name, value))

    @tool(destructive=True)
    def remove_component(project_path: ProjectPath, component: ComponentRef) -> dict:
        """Delete a component: its symbols and attached wires, its board devices and
        attached traces, and nets that become unused."""
        return svc.edit(project_path, "remove_component", lambda p: co.remove_component(p, component))

    @tool()
    def move_component(
        project_path: ProjectPath, component: ComponentRef, x_mm: float, y_mm: float,
        rotation_deg: Annotated[float | None, Field(description="New absolute rotation; "
                                                                "default: keep")] = None,
        gate: Annotated[str | None, Field(description="Symbol/gate UUID for multi-gate parts")] = None,
    ) -> dict:
        """Move (and optionally rotate) a component's schematic symbol. Wires stay attached."""
        return svc.edit(project_path, "move_component", lambda p: co.move_component(
            p, component, x_mm, y_mm, rotation_deg, gate))

    @tool()
    def set_component_value(
        project_path: ProjectPath, component: ComponentRef,
        value: Annotated[str | None, Field(description="New value text")] = None,
        attributes: Annotated[dict[str, str] | None, Field(
            description="Existing attribute name -> value, e.g. {'RESISTANCE': '330'}")] = None,
    ) -> dict:
        """Set a component's value text and/or values of its existing attributes."""
        return svc.edit(project_path, "set_value", lambda p: co.set_component_value(
            p, component, value, attributes))

    @tool()
    def connect_pins(
        project_path: ProjectPath, pin_a: PinRef, pin_b: PinRef,
        net_name: Annotated[str | None, Field(description="Net to use/create; default: existing "
                                                          "net of either pin, else auto-named")] = None,
    ) -> dict:
        """Draw a wire between two pins on the same schematic and join them to one net.
        Refuses if the pins are already on different nets."""
        return svc.edit(project_path, "connect_pins", lambda p: co.connect_pins(p, pin_a, pin_b, net_name))

    @tool()
    def disconnect_pins(project_path: ProjectPath,
                        pins: Annotated[list[str], Field(description="Pins as 'DESIGNATOR:PIN'")]) -> dict:
        """Remove all wires attached to the given pins, detach their signals from the
        net (and traces on attached pads), and delete nets left unused."""
        return svc.edit(project_path, "disconnect_pins", lambda p: co.disconnect_pins(p, pins))

    @tool()
    def create_net(project_path: ProjectPath,
                   name: Annotated[str, Field(description="Net name, e.g. 'VCC'")]) -> dict:
        """Create an (initially unconnected) named net."""
        return svc.edit(project_path, "create_net", lambda p: {"net_id": co.create_net(p, name),
                                                               "net": name})

    @tool()
    def add_net_label(
        project_path: ProjectPath, pin: PinRef,
        net_name: Annotated[str | None, Field(description="Net to attach; required if the pin "
                                                          "has no net yet")] = None,
        dx_mm: Annotated[float, Field(description="Label offset from the pin")] = 2.54,
        dy_mm: float = 0.0, rotation_deg: float = 0.0,
    ) -> dict:
        """Connect a pin to a net by name: a short wire from the pin ends in a net label.
        Pins with the same net label are connected, also across schematic pages
        (typical for GND/VCC)."""
        return svc.edit(project_path, "add_net_label", lambda p: co.add_net_label(
            p, pin, net_name, dx_mm, dy_mm, rotation_deg))
