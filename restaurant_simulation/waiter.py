"""
Waiter thread: the *producer* in the producer-consumer model.

A waiter periodically composes a random order from the menu and submits it
to the kitchen. The waiter does not know about queues or locks — it just
calls Kitchen.submit_order(), which handles dispatching items to the right
station queues.

Waiters stop on their own as soon as MAX_ORDERS have been submitted.

Random service times
--------------------
In classical queueing theory (M/M/1, M/M/c models) service durations are
modelled as an **exponential distribution**: most dishes finish near the
average, but a long tail of slow ones is possible. When ``random_times`` is
True the waiter samples each item's cook time from
``random.expovariate(1 / mean)`` instead of using the fixed menu value, so
each run will look slightly different — which is what real kitchens behave
like, and is what makes queues actually queue.
"""

import random
import threading
from time import sleep

from models import Order, OrderItem, MENU


def _sample_cook_time(mean: float, randomize: bool) -> float:
    """Return a cook time. If ``randomize`` is True, draw from an
    exponential distribution with the menu value as the mean, clamped to a
    sensible range so we never get a 0-second or 30-second steak."""
    if not randomize:
        return mean
    t = random.expovariate(1.0 / mean)
    # Clamp: at least 40% of mean, at most 2.5× mean. Without this an
    # exponential's heavy tail can stall a single dish for ages.
    return max(mean * 0.4, min(mean * 2.5, t))


class Waiter(threading.Thread):
    # How long to wait at startup before submitting the first order. Gives
    # the user a chance to open the browser before things start happening.
    INITIAL_DELAY = 2.0

    def __init__(self, name, kitchen,
                 think_time=(0.8, 2.0),
                 order_size=(1, 3),
                 random_times: bool = False):
        super().__init__(daemon=True, name=name)
        self.waiter_name = name
        self.kitchen = kitchen
        self.think_time = think_time
        self.order_size = order_size
        self.random_times = random_times
        self._menu_names = list(MENU.keys())

    def run(self):
        # Initial pause so the user can open the dashboard before the
        # first event appears.
        sleep(self.INITIAL_DELAY)
        while self.kitchen.should_produce():
            sleep(random.uniform(*self.think_time))
            if not self.kitchen.should_produce():
                break

            # Build a random order.
            items = []
            size = random.randint(*self.order_size)
            for _ in range(size):
                dish = random.choice(self._menu_names)
                station, base_cook_time = MENU[dish]
                cook_time = _sample_cook_time(base_cook_time,
                                              self.random_times)
                items.append(OrderItem(name=dish, station=station,
                                       cook_time=round(cook_time, 2)))
            order = Order(
                table=random.randint(1, 10),
                waiter_name=self.waiter_name,
                items=items,
            )
            # submit_order returns False once the order cap has been hit.
            if not self.kitchen.submit_order(order):
                break

        self.kitchen.log("WAITER_OFF",
                         f"{self.waiter_name} stopped taking orders")
