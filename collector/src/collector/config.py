from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TagConfig:
    plc_id: str
    tag_id: str
    opc_node_id: str
    unit: str | None
    data_type: str
    min_alarm: float | None
    max_alarm: float | None
    clear_margin: float
    deadband: float
    severity: str
    sampling_interval_ms: int
    updated_version: int


@dataclass(frozen=True)
class CollectorSettings:
    opc_endpoint_url: str
    cloud_api_base_url: str
    sqlite_path: str
    plc_ids: list[str] = field(default_factory=list)
    gateway_heartbeat_timeout_s: float = 15.0
    tag_config_poll_interval_s: float = 60.0
    # 2 s keeps the collector→TSDB P95 inside the 5 s budget (design §12). At the
    # 20 rows/s steady state this still batches ~40 rows per call, far under
    # transmit_batch_max.
    transmit_interval_s: float = 2.0
    transmit_batch_max: int = 500

    @classmethod
    def from_env(cls) -> "CollectorSettings":
        plc_ids_env = os.environ.get("COLLECTOR_PLC_IDS", "")
        return cls(
            opc_endpoint_url=os.environ["OPC_ENDPOINT_URL"],
            cloud_api_base_url=os.environ["CLOUD_API_BASE_URL"],
            sqlite_path=os.environ.get("COLLECTOR_SQLITE_PATH", "./collector_buffer.db"),
            plc_ids=[p for p in plc_ids_env.split(",") if p],
            gateway_heartbeat_timeout_s=float(
                os.environ.get("GATEWAY_HEARTBEAT_TIMEOUT_S", "15.0")
            ),
            tag_config_poll_interval_s=float(
                os.environ.get("TAG_CONFIG_POLL_INTERVAL_S", "60.0")
            ),
            transmit_interval_s=float(os.environ.get("TRANSMIT_INTERVAL_S", "2.0")),
            transmit_batch_max=int(os.environ.get("TRANSMIT_BATCH_MAX", "500")),
        )
