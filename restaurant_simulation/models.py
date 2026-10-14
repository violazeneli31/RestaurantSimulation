"""
Domain model: orders, items, stations, statuses, and the menu.

These are plain data classes shared between the producer (Waiter) and
the consumers (Chefs). They carry no threading logic of their own — all
synchronization lives in kitchen.Kitchen.
"""

from dataclasses import dataclass, field
from enum import Enum
from time import time
import itertools


class Station(Enum):
    """A kitchen station. Each station has its own queue and its own chefs."""
    GRILL = "grill"
    PASTA = "pasta"
    DESSERT = "dessert"


class Status(Enum):
    """Lifecycle of a single dish (and, by aggregation, of an order)."""
    WAITING = "waiting"   # in the station queue, no chef yet
    COOKING = "cooking"   # a chef is actively preparing it
    READY = "ready"       # done, waiting to be served
    SERVED = "served"     # delivered to the table (terminal state)


# Menu: dish name -> (station that cooks it, cook time in seconds).
# Cook times are slow on purpose: it lets the user see each step (waiter
# submits → item queued → chef waits for stove → chef cooks → item done →
# stove freed → order ready → delivery serves) clearly on the dashboard.
MENU = {
    "Steak":      (Station.GRILL,   4.0),
    "Burger":     (Station.GRILL,   2.5),
    "Chicken":    (Station.GRILL,   3.0),
    "Carbonara":  (Station.PASTA,   3.5),
    "Lasagna":    (Station.PASTA,   4.5),
    "Ravioli":    (Station.PASTA,   2.5),
    "Tiramisu":   (Station.DESSERT, 2.0),
    "Cheesecake": (Station.DESSERT, 2.2),
    "Ice Cream":  (Station.DESSERT, 1.5),
}


# Module-level counters give every order and item a unique id.
# itertools.count is thread-safe in CPython thanks to the GIL.
_order_id_counter = itertools.count(1)
_item_id_counter = itertools.count(1)


def reset_id_counters():
    """Reset id counters. Used by the benchmark so each run starts at #1."""
    global _order_id_counter, _item_id_counter
    _order_id_counter = itertools.count(1)
    _item_id_counter = itertools.count(1)


@dataclass
class OrderItem:
    """One dish within an order. Owned by exactly one Order."""
    name: str
    station: Station
    cook_time: float
    status: Status = Status.WAITING
    chef_name: str = ""          # filled in when a chef starts cooking it
    item_id: int = field(default_factory=lambda: next(_item_id_counter))


@dataclass
class Order:
    """A single table's order. May span multiple stations."""
    table: int
    waiter_name: str
    items: list                  # list[OrderItem]
    order_id: int = field(default_factory=lambda: next(_order_id_counter))
    created_at: float = field(default_factory=time)
    served_at: float = 0.0       # 0 until the delivery thread serves it

    @property
    def status(self) -> Status:
        """Order status is derived from the statuses of its items."""
        if self.served_at:
            return Status.SERVED
        if all(it.status == Status.READY for it in self.items):
            return Status.READY
        if any(it.status == Status.COOKING for it in self.items):
            return Status.COOKING
        return Status.WAITING
