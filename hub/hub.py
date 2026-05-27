"""
hub/hub.py - Main BLE orchestrator for sensor ingestion.
"""

import argparse
import asyncio
import math
import random
import sys
import time

import requests

try:
    from .config import (
        API_BASE,
        API_TOKEN,
        REPORT_INTERVAL,
        BLE_NAME_PREFIX,
        BLE_SCAN_TIMEOUT,
        THRESHOLDS,
        DEFAULT_THRESHOLD,
        ACCEL_SCALE,
        GYRO_SCALE,
    )
    from .imu_processor import ImuProcessor
    from .ble_scanner import scan_for_sensor, stream_imu
except ImportError:
    from config import (
        API_BASE,
        API_TOKEN,
        REPORT_INTERVAL,
        BLE_NAME_PREFIX,
        BLE_SCAN_TIMEOUT,
        THRESHOLDS,
        DEFAULT_THRESHOLD,
        ACCEL_SCALE,
        GYRO_SCALE,
    )
    from imu_processor import ImuProcessor
    from ble_scanner import scan_for_sensor, stream_imu


def _api_headers():
    return {"X-CombatIQ-Token": API_TOKEN} if API_TOKEN else None


def _post_ping(user_id: int, sensor_code: str, session_id: int | None, device_id: str = "hub-ble"):
    try:
        requests.post(
            f"{API_BASE}/api/sensor-ping",
            json={
                "user_id": user_id,
                "sensor_code": sensor_code,
                "device_id": device_id,
                "session_id": session_id,
            },
            headers=_api_headers(),
            timeout=5,
        )
    except Exception as exc:
        print(f"[API] ping error: {exc}")


def _post_data(
    user_id: int,
    sensor_code: str,
    session_id: int | None,
    metrics: dict,
    device_id: str = "hub-ble",
):
    payload = {
        "user_id": user_id,
        "sensor_code": sensor_code,
        "device_id": device_id,
        "session_id": session_id,
        **metrics,
    }
    try:
        response = requests.post(
            f"{API_BASE}/api/sensor-data",
            json=payload,
            headers=_api_headers(),
            timeout=5,
        )
        print(f"[API] data -> {response.status_code}  {metrics}")
    except Exception as exc:
        print(f"[API] data error: {exc}")


def _demo_sample(threshold_g: float):
    """Generate synthetic IMU samples with occasional peaks."""
    base_g = 0.6
    spike_g = threshold_g * 1.4
    spike_lsb = int(spike_g * ACCEL_SCALE)
    base_lsb = int(base_g * ACCEL_SCALE)
    noise = lambda: random.randint(-400, 400)

    if random.random() < 0.03:
        ax = spike_lsb + random.randint(-200, 200)
        ay = noise()
        az = noise()
        gz = int(random.uniform(180, 360) * GYRO_SCALE * random.choice([-1, 1]))
    else:
        ax = base_lsb + noise()
        ay = noise()
        az = noise()
        gz = int(random.uniform(-30, 30) * GYRO_SCALE)
    gx = noise() // 8
    gy = noise() // 8
    return ax, ay, az, gx, gy, gz


async def run_demo(user_id: int, sensor_type: str, session_id: int | None, loops: int):
    threshold = THRESHOLDS.get(sensor_type, DEFAULT_THRESHOLD)
    processor = ImuProcessor(threshold, sensor_type)
    sample_hz = 50

    _post_ping(user_id, sensor_type, session_id, device_id="hub-demo")
    print(
        f"[DEMO] user={user_id}  sensor={sensor_type}  threshold={threshold}g  "
        f"interval={REPORT_INTERVAL}s  loops={loops or 'inf'}"
    )

    loop_count = 0
    while loops == 0 or loop_count < loops:
        t0 = time.monotonic()
        while time.monotonic() - t0 < REPORT_INTERVAL:
            ax, ay, az, gx, gy, gz = _demo_sample(threshold)
            hit = processor.push(ax, ay, az, gx, gy, gz)
            if hit:
                mag = math.sqrt((ax / ACCEL_SCALE) ** 2 + (ay / ACCEL_SCALE) ** 2 + (az / ACCEL_SCALE) ** 2)
                print(f"  hit  {mag:.2f}g  {abs(gz / GYRO_SCALE):.0f} dps")
            await asyncio.sleep(1 / sample_hz)

        metrics = processor.flush_metrics()
        _post_data(user_id, sensor_type, session_id, metrics)
        loop_count += 1

    print("[DEMO] Finalizado.")


async def run_live(
    user_id: int,
    sensor_type: str,
    session_id: int | None,
    address: str,
    loops: int,
):
    threshold = THRESHOLDS.get(sensor_type, DEFAULT_THRESHOLD)
    processor = ImuProcessor(threshold, sensor_type)
    stop_event = asyncio.Event()

    _post_ping(user_id, sensor_type, session_id, device_id=address)
    print(
        f"[HUB] user={user_id}  sensor={sensor_type}  addr={address}  "
        f"threshold={threshold}g  interval={REPORT_INTERVAL}s  loops={loops or 'inf'}"
    )

    def on_sample(ax, ay, az, gx=0, gy=0, gz=0):
        hit = processor.push(ax, ay, az, gx, gy, gz)
        if hit:
            mag = math.sqrt((ax / ACCEL_SCALE) ** 2 + (ay / ACCEL_SCALE) ** 2 + (az / ACCEL_SCALE) ** 2)
            print(f"  hit  {mag:.2f}g  {abs(gz / GYRO_SCALE):.0f} dps")

    async def reporter():
        loop_count = 0
        while loops == 0 or loop_count < loops:
            await asyncio.sleep(REPORT_INTERVAL)
            metrics = processor.flush_metrics()
            _post_data(user_id, sensor_type, session_id, metrics, device_id=address)
            loop_count += 1
        stop_event.set()

    await asyncio.gather(stream_imu(address, on_sample, stop_event), reporter())
    print("[HUB] Sesion terminada.")


def _parse_args():
    parser = argparse.ArgumentParser(description="CombatIQ BLE Hub - IMU pipeline")
    parser.add_argument("--user", type=int, required=True, help="ID de usuario en CombatIQ")
    parser.add_argument("--sensor", type=str, default="IMU_WRIST", choices=list(THRESHOLDS.keys()))
    parser.add_argument("--session", type=int, default=None, help="ID de sesion activa")
    parser.add_argument("--api", type=str, default=None, help="URL base de la API")
    parser.add_argument("--address", type=str, default=None, help="Direccion BLE del ESP32")
    parser.add_argument("--scan", action="store_true", help="Auto-descubrir sensor BLE")
    parser.add_argument("--demo", action="store_true", help="Modo simulacion sin hardware")
    parser.add_argument("--loops", type=int, default=0, help="Numero de ciclos (0 = infinito)")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if args.api:
        try:
            from . import config as cfg_mod
        except ImportError:
            import config as cfg_mod
        cfg_mod.API_BASE = args.api
        API_BASE = args.api

    if args.demo:
        asyncio.run(run_demo(args.user, args.sensor, args.session, args.loops))
        sys.exit(0)

    address = args.address
    if not address:
        if args.scan:
            address = asyncio.run(scan_for_sensor())
        if not address:
            print("ERROR: especifica --address o usa --scan / --demo")
            sys.exit(1)

    asyncio.run(run_live(args.user, args.sensor, args.session, address, args.loops))
