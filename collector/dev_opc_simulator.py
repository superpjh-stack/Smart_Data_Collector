"""Standalone OPC UA simulator for manual dev testing.

Plays the role of the real OPC UA gateway: exposes one PLC/one tag
(PLC-01/TEMP_01) and randomly walks its value so the collector has live
subscription notifications to react to. Not used by the automated test
suite (see tests/test_integration_opc.py for that) -- this is only for
`python dev_opc_simulator.py` manual runs against a locally running
collector + cloud-api.
"""

from __future__ import annotations

import asyncio
import logging
import random

from asyncua import Server, ua

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dev_opc_simulator")

ENDPOINT = "opc.tcp://127.0.0.1:48401/freeopcua/collector-dev/"


async def main() -> None:
    server = Server()
    await server.init()
    server.set_endpoint(ENDPOINT)

    idx = await server.register_namespace("collector-dev")
    objects = server.nodes.objects
    node_id = ua.NodeId("Temp01", idx, ua.NodeIdType.String)
    temp_node = await objects.add_variable(node_id, "Temp01", 65.0)
    await temp_node.set_writable()

    await server.start()
    logger.info("OPC UA simulator running at %s (node: %s)", ENDPOINT, temp_node.nodeid.to_string())
    logger.info("Node ID to use in tag_config.opc_node_id: %s", temp_node.nodeid.to_string())

    value = 65.0
    try:
        while True:
            await asyncio.sleep(2.0)
            # Random walk, occasionally spiking past 80 to trigger the HIGH alarm.
            value += random.uniform(-1.5, 2.0)
            value = max(50.0, min(95.0, value))
            await temp_node.write_value(ua.Variant(round(value, 2), ua.VariantType.Double))
            logger.info("TEMP_01 = %.2f", value)
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
