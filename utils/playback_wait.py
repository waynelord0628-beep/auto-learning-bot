"""Keep cancellation responsive without polling the browser every second."""
import time


def wait_with_heartbeat(seconds, should_continue, heartbeat, interval=5.0):
    deadline = time.monotonic() + max(0.0, float(seconds))
    interval = max(1.0, float(interval))
    next_tick = min(deadline, time.monotonic() + interval)
    while should_continue():
        now = time.monotonic()
        if now >= next_tick:
            heartbeat()
            if now >= deadline:
                return should_continue()
            next_tick = min(deadline, time.monotonic() + interval)
        time.sleep(min(0.2, max(0.0, next_tick - time.monotonic())))
    return False
