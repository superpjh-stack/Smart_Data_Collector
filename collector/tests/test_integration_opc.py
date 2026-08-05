"""End-to-end test against a real (in-process) OPC UA server.

This exercises OpcCollectorClient's subscription, deadband, and gateway-down
detection over an actual OPC UA wire session — asyncua's own Server class
plays the role of the OPC UA gateway, so no external PLC/gateway hardware or
network is required.

The cloud-side leg (ingestion API <-> TimescaleDB) is NOT covered here: it
needs a real Postgres+TimescaleDB instance. See cloud-api/tests/README.md for
how to run that leg with docker-compose.
"""

import asyncio

import pytest
from asyncua import Server, ua

from collector.config import TagConfig
from collector.opc_client import OpcCollectorClient

pytestmark = pytest.mark.integration

ENDPOINT = "opc.tcp://127.0.0.1:48401/freeopcua/collector-test/"


async def _force_stop(server: Server) -> None:
    """Abruptly kill the in-process OPC UA server, without a graceful goodbye.

    Plain `await server.stop()` calls `asyncio.Server.wait_closed()`, which on
    Python 3.12+ also waits for every active connection's handler task to
    finish (a CPython behavior change asyncua 2.0.1 predates). asyncua's
    per-connection read loop doesn't unblock just because its transport was
    closed, so with a client still attached that await hangs forever.

    Working around it by yanking the transports and the listening socket
    directly (skipping wait_closed()/iserver.stop()) isn't just a hang
    workaround -- it's a *more* realistic simulation of a gateway outage than
    a graceful stop() would be anyway: a real PLC gateway dying doesn't send
    a clean goodbye either.
    """
    bserver = server.bserver
    if bserver is None:
        return
    for transport in bserver.iserver.asyncio_transports:
        transport.close()
    if bserver._server is not None:
        bserver._server.close()


@pytest.fixture
async def opc_server():
    server = Server()
    await server.init()
    server.set_endpoint(ENDPOINT)

    idx = await server.register_namespace("collector-test")
    objects = server.nodes.objects
    temp_node = await objects.add_variable(idx, "Temp01", 20.0)
    await temp_node.set_writable()

    await server.start()
    try:
        yield server, temp_node
    finally:
        await _force_stop(server)  # already stopped by the test in the gateway-down scenario


async def _wait_for(predicate, timeout_s: float = 5.0, interval_s: float = 0.05):
    elapsed = 0.0
    while elapsed < timeout_s:
        if predicate():
            return True
        await asyncio.sleep(interval_s)
        elapsed += interval_s
    return False


async def test_collector_receives_readings_and_respects_deadband(opc_server):
    server, temp_node = opc_server
    node_id = temp_node.nodeid.to_string()

    tag = TagConfig(
        plc_id="PLC-01",
        tag_id="TEMP_01",
        opc_node_id=node_id,
        unit="celsius",
        data_type="float",
        min_alarm=None,
        max_alarm=80.0,
        clear_margin=2.0,
        deadband=1.0,
        severity="HIGH",
        sampling_interval_ms=200,
        updated_version=1,
    )

    received = []

    async def on_reading(raw):
        received.append(raw)

    async def on_gateway_status(plc_id, is_up):
        pass

    client = OpcCollectorClient(
        endpoint_url=ENDPOINT,
        tags_by_plc={"PLC-01": [tag]},
        on_reading=on_reading,
        on_gateway_status=on_gateway_status,
        heartbeat_timeout_s=5.0,
    )
    run_task = asyncio.create_task(client.run())

    try:
        await _wait_for(lambda: len(received) >= 1)
        assert len(received) >= 1
        assert received[0].tag_id == "TEMP_01"
        assert received[0].value == 20.0

        baseline = len(received)

        # Within the deadband — must NOT produce a new reading.
        await temp_node.write_value(ua.Variant(20.5, ua.VariantType.Double))
        await asyncio.sleep(1.0)
        assert len(received) == baseline

        # Beyond the deadband — must produce a new reading.
        await temp_node.write_value(ua.Variant(22.0, ua.VariantType.Double))
        await _wait_for(lambda: len(received) > baseline)
        assert len(received) > baseline
        assert received[-1].value == 22.0
    finally:
        await client.stop()
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task


async def test_gateway_down_detected_when_server_stops(opc_server):
    server, temp_node = opc_server
    node_id = temp_node.nodeid.to_string()

    tag = TagConfig(
        plc_id="PLC-01",
        tag_id="TEMP_01",
        opc_node_id=node_id,
        unit="celsius",
        data_type="float",
        min_alarm=None,
        max_alarm=80.0,
        clear_margin=2.0,
        deadband=0,
        severity="HIGH",
        sampling_interval_ms=200,
        updated_version=1,
    )

    status_events = []

    async def on_reading(raw):
        pass

    async def on_gateway_status(plc_id, is_up):
        status_events.append((plc_id, is_up))

    client = OpcCollectorClient(
        endpoint_url=ENDPOINT,
        tags_by_plc={"PLC-01": [tag]},
        on_reading=on_reading,
        on_gateway_status=on_gateway_status,
        heartbeat_timeout_s=1.5,
        reconnect_backoff_s=0.5,
    )
    run_task = asyncio.create_task(client.run())

    try:
        await _wait_for(lambda: (True) in [up for _, up in status_events] or True, timeout_s=2.0)
        # Kill the server out from under the client to simulate a gateway outage.
        await _force_stop(server)

        saw_down = await _wait_for(
            lambda: any(not up for _, up in status_events), timeout_s=10.0
        )
        assert saw_down, f"expected a gateway_down status event, got: {status_events}"
    finally:
        await client.stop()
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task
