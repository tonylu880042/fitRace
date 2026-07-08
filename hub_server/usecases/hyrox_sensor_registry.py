"""Hyrox sensor registry and normalized telemetry events.

Phase 2 of the architecture plan (docs/hyrox_system_architecture_plan.md):
resolve raw telemetry addresses into resource-aware events. This layer is
standalone -- it does not yet feed the live MQTT ingestion path. Downstream
consumers (assignment attribution, stage reducers) arrive in Phase 3/4.
"""

from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel, Field

from hub_server.domain.models import HyroxStage
from hub_server.domain.hyrox_venue import (
    HyroxResourceGroup,
    HyroxResourceUnit,
    HyroxSensorClass,
    HyroxVenueConfig,
)

# RFID endpoint roles carried on the normalized event.
START_LINE = "start_line"
FINISH_LINE = "finish_line"
ENTRY_GATE = "entry_gate"


@dataclass(frozen=True)
class RfidResolution:
    resource_id: str
    group_id: str
    role: str  # START_LINE | FINISH_LINE | ENTRY_GATE
    sensor_class: HyroxSensorClass
    stage_candidates: tuple[HyroxStage, ...]


@dataclass(frozen=True)
class NodeResolution:
    resource_id: str
    group_id: str
    sensor_class: HyroxSensorClass
    stage_candidates: tuple[HyroxStage, ...]


class HyroxTelemetryEvent(BaseModel):
    """Resource-aware event; all downstream logic consumes this, not raw MQTT."""

    sensor_class: HyroxSensorClass
    resource_group_id: str
    resource_id: str
    endpoint: Optional[str] = None  # RFID role; None for node/FTMS events
    tag_id: Optional[str] = None    # present for RFID; None for anonymous FTMS
    timestamp_epoch_ms: int
    metrics: Optional[dict] = None  # FTMS/rep metrics
    raw_payload: dict = Field(default_factory=dict)


class HyroxSensorRegistry:
    """Indexes a venue config for O(1) sensor-address resolution."""

    def __init__(self, venue: HyroxVenueConfig):
        self._rfid: dict[tuple[str, str], RfidResolution] = {}
        self._nodes: dict[str, NodeResolution] = {}

        for group in venue.resource_groups:
            for unit in group.units:
                self._index_rfid(group, unit)
                self._index_node(group, unit)

    def _index_rfid(self, group: HyroxResourceGroup, unit: HyroxResourceUnit):
        stage_candidates = tuple(group.stage_candidates)
        endpoints = (
            (START_LINE, unit.start_endpoint),
            (FINISH_LINE, unit.finish_endpoint),
            (ENTRY_GATE, unit.entry_gate),
        )
        for role, ep in endpoints:
            if ep is None:
                continue
            key = (ep.node_id, ep.antenna_id)
            if key in self._rfid:
                raise ValueError(
                    f"Duplicate RFID read zone {key[0]}/{key[1]} for "
                    f"{self._rfid[key].resource_id} and {unit.resource_id}"
                )
            self._rfid[key] = RfidResolution(
                resource_id=unit.resource_id,
                group_id=group.group_id,
                role=role,
                sensor_class=unit.sensor_class,
                stage_candidates=stage_candidates,
            )

    def _index_node(self, group: HyroxResourceGroup, unit: HyroxResourceUnit):
        # Node-addressed sensors (FTMS machines, rep counters) resolve by node_id.
        if not unit.node_id:
            return
        if unit.sensor_class not in (
            HyroxSensorClass.FTMS_MACHINE,
            HyroxSensorClass.REP_COUNTER,
        ):
            return
        if unit.node_id in self._nodes:
            raise ValueError(
                f"Duplicate node {unit.node_id} for "
                f"{self._nodes[unit.node_id].resource_id} and {unit.resource_id}"
            )
        self._nodes[unit.node_id] = NodeResolution(
            resource_id=unit.resource_id,
            group_id=group.group_id,
            sensor_class=unit.sensor_class,
            stage_candidates=tuple(group.stage_candidates),
        )

    # --- Resolution ---

    def resolve_rfid(self, node_id: str, antenna_id: str) -> Optional[RfidResolution]:
        return self._rfid.get((node_id, antenna_id))

    def resolve_node(self, node_id: str) -> Optional[NodeResolution]:
        return self._nodes.get(node_id)

    # --- Normalization (raw address -> resource-aware event, None if unknown) ---

    def normalize_rfid(
        self,
        node_id: str,
        antenna_id: str,
        tag_id: str,
        timestamp_epoch_ms: int,
        raw_payload: Optional[dict] = None,
    ) -> Optional[HyroxTelemetryEvent]:
        res = self.resolve_rfid(node_id, antenna_id)
        if res is None:
            return None
        return HyroxTelemetryEvent(
            sensor_class=res.sensor_class,
            resource_group_id=res.group_id,
            resource_id=res.resource_id,
            endpoint=res.role,
            tag_id=tag_id,
            timestamp_epoch_ms=timestamp_epoch_ms,
            raw_payload=raw_payload or {},
        )

    def normalize_node(
        self,
        node_id: str,
        timestamp_epoch_ms: int,
        metrics: Optional[dict] = None,
        raw_payload: Optional[dict] = None,
    ) -> Optional[HyroxTelemetryEvent]:
        res = self.resolve_node(node_id)
        if res is None:
            return None
        return HyroxTelemetryEvent(
            sensor_class=res.sensor_class,
            resource_group_id=res.group_id,
            resource_id=res.resource_id,
            endpoint=None,
            tag_id=None,
            timestamp_epoch_ms=timestamp_epoch_ms,
            metrics=metrics,
            raw_payload=raw_payload or {},
        )
