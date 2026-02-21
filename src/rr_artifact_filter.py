"""
Online RR interval artifact filter for motion spikes.
Std-based threshold + Hampel (median/MAD) cleaning; interpolate with optional noise or drop.
"""

import logging
import math
import random
from collections import deque
from statistics import median, mean, stdev

logger = logging.getLogger(__name__)


def _stdev_safe(buf: list[float]) -> float:
    """Standard deviation of buf; at least 2 values. Returns 0 if len < 2."""
    if len(buf) < 2:
        return 0.0
    return stdev(buf)


class RrArtifactFilter:
    """
    Online processing of each incoming RR (ms). Outputs cleaned RR or None (drop).
    Sequential: extreme bounds -> grace period -> std-based threshold -> Hampel filter.
    """

    def __init__(
        self,
        thresh_percent: float = 0.40,
        min_rr_ms: float = 250,
        max_rr_ms: float = 2200,
        hampel_window: int = 11,
        hampel_sigma: float = 4.5,
        disable_hampel: bool = False,
        buffer_maxlen: int = 30,
        grace_beats: int = 30,
        interp_noise: bool = True,
        noise_dynamic: bool = True,
        twitchy: bool = False,
        logger_instance: logging.Logger | None = None,
    ):
        if twitchy:
            thresh_percent = 0.50
            hampel_sigma = 6.0
            grace_beats = 15
        self.thresh_percent = thresh_percent
        self.min_rr_ms = min_rr_ms
        self.max_rr_ms = max_rr_ms
        self.hampel_window = hampel_window
        self.hampel_sigma = hampel_sigma
        self.disable_hampel = disable_hampel
        self.grace_beats = grace_beats
        self.interp_noise = interp_noise
        self.noise_dynamic = noise_dynamic
        self.twitchy = twitchy
        self.interp_ratio_hint: float = 0.0
        self._logger = logger_instance or logger
        self.rr_buffer: deque[float] = deque(maxlen=buffer_maxlen)
        self.dropped = 0
        self.interpolated = 0
        self.last_was_interpolated = False

    def process_rr(self, rr_ms: float) -> float | None:
        if not isinstance(rr_ms, (int, float)):
            self.last_was_interpolated = False
            return None
        if math.isnan(rr_ms):
            self.last_was_interpolated = False
            return None

        orig_rr = float(rr_ms)

        # 1. Extreme bounds
        if rr_ms < self.min_rr_ms or rr_ms > self.max_rr_ms:
            self.dropped += 1
            self.last_was_interpolated = False
            self._logger.debug("Dropped extreme RR: %sms", rr_ms)
            return None

        # 2. Grace period: pass through and add to buffer
        if len(self.rr_buffer) < self.grace_beats:
            self.rr_buffer.append(rr_ms)
            self.last_was_interpolated = False
            return rr_ms

        # 3. Std-based threshold: delta > thresh_percent * buffer_std -> interpolate
        buf_list = list(self.rr_buffer)
        buffer_mean = mean(buf_list)
        buffer_std = _stdev_safe(buf_list)
        # Floor so stable rhythm (small std) doesn't over-flag natural variation (~5% of mean)
        buffer_std_eff = max(buffer_std, 0.05 * buffer_mean)
        delta = abs(rr_ms - buffer_mean)
        if delta > self.thresh_percent * buffer_std_eff:
            rr_clean = self._interpolate(buffer_mean)
            self.rr_buffer.append(rr_clean)
            self.interpolated += 1
            self.last_was_interpolated = True
            self._logger.info(
                "Cleaned RR spike (threshold): %.0fms -> %.0fms (mean=%.0f std=%.1f)",
                orig_rr,
                rr_clean,
                buffer_mean,
                buffer_std_eff,
            )
            return rr_clean

        self.rr_buffer.append(rr_ms)
        self.last_was_interpolated = False

        # 4. Hampel filter (optional)
        if self.disable_hampel:
            return rr_ms
        if len(self.rr_buffer) >= self.hampel_window:
            window = list(self.rr_buffer)[-self.hampel_window :]
            med_win = median(window)
            deviations = [abs(x - med_win) for x in window]
            mad = median(deviations)
            if mad < 1e-9:
                mad = 0.001
            mad_scaled = 1.4826 * mad
            mad_eff = max(mad_scaled, 5.0)
            if abs(rr_ms - med_win) > self.hampel_sigma * mad_eff:
                rr_clean = self._interpolate(median(window[-5:]) if len(window) >= 5 else med_win)
                self.rr_buffer.pop()
                self.rr_buffer.append(rr_clean)
                self.interpolated += 1
                self.last_was_interpolated = True
                self._logger.info(
                    "Cleaned RR spike (Hampel): %.0fms -> %.0fms",
                    orig_rr,
                    rr_clean,
                )
                return rr_clean

        return rr_ms

    def _interpolate(self, center_ms: float) -> float:
        """Interpolated value: center with optional noise (twitchy=0.02 fixed, else dynamic)."""
        if self.interp_noise:
            if self.twitchy:
                noise_std = 0.02
            elif self.noise_dynamic:
                noise_std = 0.02 + 0.015 * self.interp_ratio_hint
            else:
                noise_std = 0.015
            noise = 1.0 + random.gauss(0, noise_std)
            return center_ms * max(0.97, min(1.03, noise))
        return center_ms

    def stats(self) -> dict[str, int]:
        return {"dropped": self.dropped, "interpolated": self.interpolated}
