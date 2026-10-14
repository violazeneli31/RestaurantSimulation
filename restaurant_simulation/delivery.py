"""
Delivery thread: takes completed orders out of the kitchen.

Instead of busy-waiting on the order book, this thread sleeps on a
condition variable that chefs notify when they mark the *last* item of an
order READY. Wake-up is therefore event-driven, not polling.
"""

import threading
from time import sleep


class Delivery(threading.Thread):
    # Default seconds to carry the food to the table. The live demo wants
    # this slow enough to see; the benchmark overrides it with a small
    # value so delivery doesn't dominate the wall time.
    SERVE_TIME = 1.5

    def __init__(self, kitchen, serve_time=None):
        super().__init__(daemon=True, name="Delivery")
        self.kitchen = kitchen
        self.serve_time = self.SERVE_TIME if serve_time is None else serve_time

    def run(self):
        while self.kitchen.is_running():
            # Block until an order is fully READY. The timeout lets us
            # re-check the running flag periodically.
            order = self.kitchen.wait_for_ready_order(timeout=0.4)
            if order is None:
                continue
            self.kitchen.log(
                "READY",
                f"Order #{order.order_id} ready — delivery on the way")
            sleep(self.serve_time)
            self.kitchen.serve_order(order)
