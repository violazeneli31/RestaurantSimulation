"""
Kitchen: the shared-state hub that all threads coordinate through.

This is the only module that owns synchronization primitives. Waiters, Chefs
and the Delivery thread call methods on a single Kitchen instance; they never
share state with each other directly.

Primitives used here and why:
  * queue.Queue (per station)  -> classic bounded producer-consumer buffer.
    Blocks the waiter when the kitchen is overwhelmed (back-pressure).
  * threading.Semaphore         -> models physical limited equipment (a small
    number of stoves shared by every chef who needs heat).
  * threading.Lock              -> mutex around the in-memory order book,
    stats and event log so the dashboard never sees a half-updated state.
  * threading.Condition         -> the delivery thread sleeps on it and is
    woken the moment an order's last item becomes READY.

The simulation terminates after a fixed number of orders is served
(MAX_ORDERS). That makes the run short and the numbers easy to compare.
"""

import threading
from collections import deque
from queue import Empty, Full, Queue
from time import time

from models import Station, Status


# Sentinel object pushed onto station queues at shutdown so chefs can unblock
# from Queue.get() and exit cleanly.
_SHUTDOWN = object()


class Kitchen:
    # ---- Configurable capacities ------------------------------------------
    QUEUE_CAPACITY = 10   # max items waiting per station before back-pressure
    NUM_STOVES = 2        # shared by GRILL and PASTA chefs
    MAX_ORDERS = 12       # simulation stops after this many orders are served

    SHUTDOWN = _SHUTDOWN  # exposed so chef.py can recognize the sentinel

    def __init__(self, max_orders=None, num_stoves=None,
                 queue_capacity=None):
        if max_orders is not None:
            self.MAX_ORDERS = max_orders
        if num_stoves is not None:
            self.NUM_STOVES = num_stoves
        if queue_capacity is not None:
            self.QUEUE_CAPACITY = queue_capacity

        # One bounded queue per station => three independent producer-consumer
        # pipelines that share the same producers (the waiters).
        self.station_queues = {
            Station.GRILL:   Queue(maxsize=self.QUEUE_CAPACITY),
            Station.PASTA:   Queue(maxsize=self.QUEUE_CAPACITY),
            Station.DESSERT: Queue(maxsize=self.QUEUE_CAPACITY),
        }

        # Limited physical resource expressed as a counting semaphore.
        self.stoves = threading.Semaphore(self.NUM_STOVES)

        # In-flight orders (not yet served).
        self.active_orders: dict = {}         # order_id -> Order
        self.served_orders: list = []         # most-recent-first

        # The delivery thread wait()s on this condition variable; chefs
        # notify_all() when an order's last item becomes READY.
        self.order_ready_cv = threading.Condition()

        # Guards self.active_orders, self.served_orders, self.chef_status,
        # self.stats, self.event_log. Held very briefly.
        self.state_lock = threading.Lock()

        # Live snapshot of what each chef is doing — populated by chef threads,
        # read by /api/state.
        self.chef_status: dict = {}           # chef_name -> {station, doing}

        # Rolling log of "things that just happened" — the most useful panel
        # in the dashboard for understanding what threads are actually doing.
        self.event_log: deque = deque(maxlen=120)

        # Aggregated counters for the dashboard.
        self.stats = {
            "total_orders": 0,        # how many orders have been submitted
            "served_orders": 0,       # how many have been delivered
            "total_wait_time": 0.0,   # for computing average wait
        }

        # Simulation lifecycle.
        self.sim_start_time = time()
        self.sim_end_time = None      # set when MAX_ORDERS have been served
        self._running = True

    # ------------------------------------------------------------------ #
    #  Event log (used by every thread)                                  #
    # ------------------------------------------------------------------ #
    def log(self, kind: str, message: str):
        """Append an event to the rolling log. Each event is timestamped
        relative to the start of the simulation."""
        t = round(time() - self.sim_start_time, 2)
        with self.state_lock:
            self.event_log.appendleft({"t": t, "kind": kind, "msg": message})

    # ------------------------------------------------------------------ #
    #  API used by Waiter (producer)                                     #
    # ------------------------------------------------------------------ #
    def should_produce(self) -> bool:
        """Waiters call this before submitting. Returns False once enough
        orders are already in the system."""
        with self.state_lock:
            return (self._running
                    and self.stats["total_orders"] < self.MAX_ORDERS)

    def submit_order(self, order) -> bool:
        """Register an order and dispatch each item to its station's queue.
        Returns False if the limit was already reached. Putting onto a full
        Queue blocks — back-pressure."""
        with self.state_lock:
            if not self._running or self.stats["total_orders"] >= self.MAX_ORDERS:
                return False
            self.active_orders[order.order_id] = order
            self.stats["total_orders"] += 1
            count = self.stats["total_orders"]
        items_desc = ", ".join(it.name for it in order.items)
        self.log("ORDER",
                 f"{order.waiter_name} submitted order #{order.order_id} "
                 f"(table {order.table}): {items_desc}   "
                 f"[{count}/{self.MAX_ORDERS}]")
        for item in order.items:
            # Blocks while the station queue is full (back-pressure), but
            # gives up if the simulation is stopped in the meantime.
            while True:
                try:
                    self.station_queues[item.station].put((order, item),
                                                          timeout=0.3)
                    break
                except Full:
                    if not self._running:
                        return False
            self.log("QUEUE",
                     f"  ↳ '{item.name}' queued at {item.station.value} station")
        return True

    # ------------------------------------------------------------------ #
    #  API used by Chef (consumer)                                       #
    # ------------------------------------------------------------------ #
    def set_chef_status(self, name, station, doing):
        with self.state_lock:
            self.chef_status[name] = {
                "station": station.value, "doing": doing, "since": time(),
            }

    def mark_item_cooking(self, order, item, chef_name):
        with self.state_lock:
            item.status = Status.COOKING
            item.chef_name = chef_name

    def mark_item_ready(self, order, item):
        """Mark an item READY and, if the whole order is now done, wake the
        delivery thread."""
        with self.order_ready_cv:
            item.status = Status.READY
            if all(it.status == Status.READY for it in order.items):
                self.order_ready_cv.notify_all()

    # ------------------------------------------------------------------ #
    #  API used by Delivery                                              #
    # ------------------------------------------------------------------ #
    def wait_for_ready_order(self, timeout=0.5):
        """Block until at least one active order has every item READY.
        Returns one such order, or None on timeout / shutdown."""
        with self.order_ready_cv:
            self.order_ready_cv.wait_for(
                lambda: (not self._running) or self._any_ready_unlocked(),
                timeout=timeout,
            )
            if not self._running:
                return None
            for order in self.active_orders.values():
                if order.status == Status.READY:
                    return order
        return None

    def _any_ready_unlocked(self):
        return any(o.status == Status.READY for o in self.active_orders.values())

    def serve_order(self, order):
        """Move an order from active -> served, update stats, and check
        whether we have hit the termination threshold."""
        now = time()
        with self.state_lock:
            order.served_at = now
            self.active_orders.pop(order.order_id, None)
            self.served_orders.insert(0, order)
            if len(self.served_orders) > 15:
                self.served_orders = self.served_orders[:15]
            self.stats["served_orders"] += 1
            self.stats["total_wait_time"] += (now - order.created_at)
            served = self.stats["served_orders"]
        self.log("SERVED",
                 f"Order #{order.order_id} served to table {order.table}   "
                 f"[{served}/{self.MAX_ORDERS}]")
        if served >= self.MAX_ORDERS:
            self._finish()

    def _finish(self):
        """Mark simulation done and wake up all threads so they exit."""
        with self.state_lock:
            if self.sim_end_time is not None:
                return
            self.sim_end_time = time()
            self._running = False
            elapsed = round(self.sim_end_time - self.sim_start_time, 2)
        self.log("DONE",
                 f"Simulation finished — {self.MAX_ORDERS} orders served "
                 f"in {elapsed}s")
        # Empty the queues (releases any waiter blocked on a full queue when
        # the simulation is restarted mid-run), then unblock waiting chefs.
        for q in self.station_queues.values():
            while True:
                try:
                    q.get_nowait()
                except Empty:
                    break
            for _ in range(8):
                try:
                    q.put_nowait((None, _SHUTDOWN))
                except Exception:
                    break
        # Unblock the delivery thread's wait_for_ready_order
        with self.order_ready_cv:
            self.order_ready_cv.notify_all()

    # ------------------------------------------------------------------ #
    #  API for Flask                                                     #
    # ------------------------------------------------------------------ #
    def snapshot(self):
        """JSON-serializable view of the current kitchen state."""
        now = time()
        with self.state_lock:
            active = [self._order_to_dict(o) for o in self.active_orders.values()]
            served = [self._order_to_dict(o) for o in self.served_orders]
            chefs = dict(self.chef_status)
            stats = dict(self.stats)
            events = list(self.event_log)[:40]
            start = self.sim_start_time
            end = self.sim_end_time

        active.sort(key=lambda o: o["order_id"])
        elapsed = round((end or now) - start, 2)
        avg_wait = (stats["total_wait_time"] / stats["served_orders"]
                    if stats["served_orders"] else 0.0)
        throughput = (stats["served_orders"] / elapsed * 60.0
                      if elapsed > 0 else 0.0)

        stats["avg_wait_time"] = round(avg_wait, 2)
        stats["elapsed"] = elapsed
        stats["throughput"] = round(throughput, 1)
        stats["max_orders"] = self.MAX_ORDERS
        stats["num_stoves"] = self.NUM_STOVES
        stats["queue_capacity"] = self.QUEUE_CAPACITY
        stats["done"] = end is not None
        # After termination, any items remaining in queues are shutdown
        # sentinels, not real orders — report 0 to avoid confusion.
        if end is not None:
            stats["queue_depths"] = {s.value: 0 for s in self.station_queues}
        else:
            stats["queue_depths"] = {s.value: q.qsize()
                                     for s, q in self.station_queues.items()}
        return {"active": active, "served": served, "chefs": chefs,
                "stats": stats, "events": events}

    @staticmethod
    def _order_to_dict(o):
        return {
            "order_id": o.order_id,
            "table": o.table,
            "waiter": o.waiter_name,
            "status": o.status.value,
            "items": [
                {"name": it.name, "station": it.station.value,
                 "status": it.status.value, "chef": it.chef_name}
                for it in o.items
            ],
        }

    def is_running(self) -> bool:
        return self._running
