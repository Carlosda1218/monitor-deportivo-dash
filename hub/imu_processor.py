"""
hub/imu_processor.py - Real-time IMU sample processor.
"""

import math
import time

try:
    from .config import ACCEL_SCALE, GYRO_SCALE
except ImportError:
    from config import ACCEL_SCALE, GYRO_SCALE


class ImuProcessor:
    def __init__(self, threshold_g: float, sensor_type: str = "IMU"):
        self.threshold = threshold_g
        self.sensor_type = sensor_type

        self._hits: list[float] = []
        self._max_g: float = 0.0
        self._sum_g: float = 0.0
        self._max_angvel: float = 0.0
        self._sum_angvel: float = 0.0
        self._n_samples: int = 0
        self._above: bool = False
        self._start: float = time.monotonic()

    def push(self, ax: int, ay: int, az: int, gx: int = 0, gy: int = 0, gz: int = 0) -> bool:
        """Ingest one raw sample and return True when a hit is detected."""
        ax_g = ax / ACCEL_SCALE
        ay_g = ay / ACCEL_SCALE
        az_g = az / ACCEL_SCALE
        mag = math.sqrt(ax_g * ax_g + ay_g * ay_g + az_g * az_g)
        gz_dps = abs(gz / GYRO_SCALE)

        self._n_samples += 1
        self._sum_g += mag
        self._sum_angvel += gz_dps
        if mag > self._max_g:
            self._max_g = mag
        if gz_dps > self._max_angvel:
            self._max_angvel = gz_dps

        hit = False
        if mag >= self.threshold:
            if not self._above:
                self._above = True
                self._hits.append(time.monotonic())
                hit = True
        else:
            self._above = False

        return hit

    def flush_metrics(self) -> dict:
        """Return accumulated metrics and reset the internal state."""
        now = time.monotonic()
        elapsed = now - self._start or 1.0
        n_hits = len(self._hits)
        hpm = round(n_hits / (elapsed / 60), 1) if elapsed >= 5 else 0.0
        mean_g = round(self._sum_g / self._n_samples, 3) if self._n_samples else 0.0
        mean_av = round(self._sum_angvel / self._n_samples, 1) if self._n_samples else 0.0
        max_g = round(self._max_g, 3)
        max_av = round(self._max_angvel, 1)

        self._hits.clear()
        self._max_g = 0.0
        self._sum_g = 0.0
        self._max_angvel = 0.0
        self._sum_angvel = 0.0
        self._n_samples = 0
        self._start = now

        return {
            "n_hits": n_hits,
            "hits_per_min": hpm,
            "mean_int_g": mean_g,
            "max_int_g": max_g,
            "mean_ang_vel": mean_av,
            "max_ang_vel": max_av,
        }
