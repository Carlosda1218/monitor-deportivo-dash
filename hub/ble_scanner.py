"""
hub/ble_scanner.py - BLE scan and streaming helpers.
"""

import asyncio
import struct
from typing import Callable

try:
    from .config import NUS_SERVICE, NUS_TX, BLE_NAME_PREFIX, BLE_SCAN_TIMEOUT
except ImportError:
    from config import NUS_SERVICE, NUS_TX, BLE_NAME_PREFIX, BLE_SCAN_TIMEOUT

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    BleakClient = None
    BleakScanner = None


PACKET_FMT = "<6h"
PACKET_SIZE = struct.calcsize(PACKET_FMT)


def _ensure_bleak():
    if BleakScanner is None or BleakClient is None:
        raise RuntimeError("bleak no esta instalado. Ejecuta: pip install -r hub/requirements.txt")


async def scan_for_sensor(
    timeout: float = BLE_SCAN_TIMEOUT,
    name_prefix: str = BLE_NAME_PREFIX,
) -> str | None:
    """Return the BLE address of the first sensor matching the expected prefix."""
    _ensure_bleak()
    print(f"[BLE] Escaneando {timeout}s buscando '{name_prefix}-*' ...")
    devices = await BleakScanner.discover(timeout=timeout)
    for device in devices:
        if device.name and device.name.startswith(name_prefix):
            print(f"[BLE] Encontrado: {device.name}  {device.address}")
            return device.address
    print("[BLE] No se encontro ningun sensor.")
    return None


async def stream_imu(
    address: str,
    on_sample: Callable[[int, int, int], None],
    stop_event: asyncio.Event | None = None,
) -> None:
    """Connect to a BLE sensor and stream decoded IMU packets."""
    _ensure_bleak()

    def handle_notify(_char, data: bytearray):
        if len(data) < PACKET_SIZE:
            return
        ax, ay, az, gx, gy, gz = struct.unpack(PACKET_FMT, data[:PACKET_SIZE])
        on_sample(ax, ay, az, gx, gy, gz)

    print(f"[BLE] Conectando a {address} ...")
    async with BleakClient(address) as client:
        print("[BLE] Conectado. Suscribiendo a notificaciones IMU ...")
        await client.start_notify(NUS_TX, handle_notify)
        try:
            if stop_event:
                await stop_event.wait()
            else:
                await asyncio.sleep(3600)
        finally:
            await client.stop_notify(NUS_TX)
    print("[BLE] Desconectado.")
