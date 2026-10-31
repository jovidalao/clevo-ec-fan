"""Bounded, timestamped session history and peak-preserving chart reduction."""
from collections import deque

HISTORY_RANGES = {"3 分钟": 180, "15 分钟": 900, "1 小时": 3600,
                  "6 小时": 21600, "24 小时": 86400}
RETENTION_SECONDS = 86400
MAX_SAMPLES_PER_FAN = 60000
MAX_SAMPLE_GAP = 8


class TelemetryHistory:
    def __init__(self):
        self.fans = {fan: deque(maxlen=MAX_SAMPLES_PER_FAN) for fan in (1, 2)}

    def append(self, timestamp, readings):
        cutoff = timestamp - RETENTION_SECONDS
        for entries in self.fans.values():
            while entries and entries[0]["timestamp"] < cutoff:
                entries.popleft()
        for reading in readings:
            fan = reading["fan"]
            if fan in self.fans:
                self.fans[fan].append({**reading, "timestamp": timestamp})

    def segments(self, fan, metric, end, duration, pixels):
        """Keep each pixel column's first, min, max and last; never bridge gaps."""
        start = end - duration
        result, segment, bucket = [], [], []
        bucket_index = None
        previous = None

        def flush_bucket():
            if bucket:
                selected = {0, len(bucket) - 1,
                            min(range(len(bucket)), key=lambda i: bucket[i][1]),
                            max(range(len(bucket)), key=lambda i: bucket[i][1])}
                segment.extend(bucket[i] for i in sorted(selected))
                bucket.clear()

        def flush_segment():
            flush_bucket()
            if segment:
                result.append(list(segment))
                segment.clear()

        for entry in self.fans[fan]:
            timestamp = entry["timestamp"]
            if timestamp < start or timestamp > end:
                continue
            value = entry.get(metric)
            if value is None:
                flush_segment()
                previous = None
                bucket_index = None
                continue
            if previous is not None and timestamp - previous > MAX_SAMPLE_GAP:
                flush_segment()
                bucket_index = None
            index = min(max(1, pixels) - 1, int((timestamp - start) / duration * max(1, pixels)))
            if index != bucket_index:
                flush_bucket()
                bucket_index = index
            bucket.append((timestamp, value))
            previous = timestamp
        flush_segment()
        return result
