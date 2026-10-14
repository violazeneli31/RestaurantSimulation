import threading
import time

import pytest

from benchmark import build_orders, run_parallel, run_sequential
from chef import Chef
from delivery import Delivery
from kitchen import Kitchen
from models import Station, Status
from waiter import Waiter


class CountingSemaphore:
    """Wraps a semaphore and records the highest number of holders at once."""

    def __init__(self, n):
        self._sem = threading.Semaphore(n)
        self._lock = threading.Lock()
        self.current = 0
        self.max_seen = 0

    def acquire(self):
        self._sem.acquire()
        with self._lock:
            self.current += 1
            self.max_seen = max(self.max_seen, self.current)

    def release(self):
        with self._lock:
            self.current -= 1
        self._sem.release()


def run_kitchen(orders, stoves=2, chefs=(2, 2, 1), wrap_stoves=False):
    kitchen = Kitchen(max_orders=len(orders), num_stoves=stoves)
    if wrap_stoves:
        kitchen.stoves = CountingSemaphore(stoves)
    cooked = []
    original = kitchen.mark_item_cooking

    def record(order, item, chef):
        cooked.append(item.item_id)
        original(order, item, chef)

    kitchen.mark_item_cooking = record
    roster = ([(f"G{i}", Station.GRILL) for i in range(chefs[0])]
              + [(f"P{i}", Station.PASTA) for i in range(chefs[1])]
              + [(f"D{i}", Station.DESSERT) for i in range(chefs[2])])
    threads = [Chef(n, s, kitchen) for n, s in roster] + [Delivery(kitchen, serve_time=0.01)]
    for t in threads:
        t.start()
    for order in orders:
        kitchen.submit_order(order)
    deadline = time.time() + 20
    while kitchen.is_running() and time.time() < deadline:
        time.sleep(0.02)
    for t in threads:
        t.join(timeout=2)
    return kitchen, cooked, threads


def test_every_order_is_served_and_every_dish_cooked_once():
    orders = build_orders(10, time_scale=0.02)
    kitchen, cooked, _ = run_kitchen(orders)
    assert not kitchen.is_running()
    assert kitchen.stats["served_orders"] == 10
    all_items = [it.item_id for o in orders for it in o.items]
    assert sorted(cooked) == sorted(all_items)  # no dish skipped or cooked twice
    assert all(o.status == Status.SERVED for o in orders)


@pytest.mark.parametrize("stoves", [1, 2, 3])
def test_stove_limit_is_never_exceeded(stoves):
    orders = build_orders(12, time_scale=0.02)
    kitchen, _, _ = run_kitchen(orders, stoves=stoves, chefs=(3, 3, 1), wrap_stoves=True)
    assert kitchen.stoves.max_seen <= stoves
    assert kitchen.stoves.max_seen == stoves  # with 6 stove chefs, all stoves get used


def test_all_threads_stop_when_simulation_ends():
    orders = build_orders(6, time_scale=0.02)
    _, _, threads = run_kitchen(orders)
    assert not any(t.is_alive() for t in threads)


def test_restart_mid_run_releases_blocked_threads(monkeypatch):
    # Tiny queue + fast waiters, so waiters block on a full queue.
    import waiter
    monkeypatch.setattr(waiter, "MENU", {k: (s, t * 0.05) for k, (s, t) in waiter.MENU.items()})
    monkeypatch.setattr(Waiter, "INITIAL_DELAY", 0)
    kitchen = Kitchen(max_orders=50, num_stoves=1, queue_capacity=1)
    waiters = [Waiter(f"W{i}", kitchen, think_time=(0, 0.01)) for i in range(3)]
    chefs = [Chef("G", Station.GRILL, kitchen), Chef("P", Station.PASTA, kitchen),
             Chef("D", Station.DESSERT, kitchen)]
    threads = waiters + chefs + [Delivery(kitchen, serve_time=0.01)]
    for t in threads:
        t.start()
    time.sleep(0.5)
    kitchen._finish()  # what /api/restart does to the old simulation
    for t in threads:
        t.join(timeout=3)
    assert not any(t.is_alive() for t in threads)


def test_parallel_kitchen_is_faster_than_sequential():
    t_seq = run_sequential(build_orders(8, time_scale=0.05))
    t_par = run_parallel(build_orders(8, time_scale=0.05))
    assert t_seq / t_par > 1.3
