"""
Speedup benchmark: sequential vs. parallel kitchen.

The same set of orders is processed twice:
  1. SEQUENTIAL — one fake "chef" loops over every item one by one.
     This is the baseline: no threads, no concurrency.
  2. PARALLEL   — the real kitchen with multiple chef threads, queues,
     a semaphore and a condition variable.

We measure the wall-clock time of each run and report the speedup:

        speedup = time_sequential  /  time_parallel

Anything above 1.0 means the parallel version is faster.

To make the comparison fair, both runs use the exact same orders
(produced from a fixed random seed).
"""

import random
from time import sleep, time

from models import (
    MENU, Order, OrderItem, Station, reset_id_counters,
)
from kitchen import Kitchen
from chef import Chef
from delivery import Delivery


# --- Order generation --------------------------------------------------------
def build_orders(num_orders: int, seed: int = 42, time_scale: float = 0.35):
    """Generate a deterministic list of orders so both benchmark runs
    process the *same* work.

    `time_scale` shrinks each dish's cook time. The live demo uses slow cook
    times (3–5s) so events are easy to watch, but for the benchmark we want
    something quick — otherwise the sequential run would take a minute.
    """
    rng = random.Random(seed)
    menu_names = list(MENU.keys())
    reset_id_counters()
    orders = []
    for _ in range(num_orders):
        items = []
        for _ in range(rng.randint(1, 3)):
            dish = rng.choice(menu_names)
            station, cook_time = MENU[dish]
            items.append(OrderItem(name=dish, station=station,
                                   cook_time=cook_time * time_scale))
        orders.append(Order(
            table=rng.randint(1, 10),
            waiter_name="bench",
            items=items,
        ))
    return orders


# --- Sequential baseline -----------------------------------------------------
def run_sequential(orders):
    """No threads. One worker processes every item end-to-end."""
    start = time()
    for order in orders:
        for item in order.items:
            sleep(item.cook_time)   # the "cooking" work
    return round(time() - start, 2)


# --- Parallel run ------------------------------------------------------------
def run_parallel(orders, num_stoves: int = 2, grill_chefs: int = 2,
                 pasta_chefs: int = 2, dessert_chefs: int = 1):
    """Run the orders through the real multi-threaded kitchen."""
    # Reset id counters so each run starts at #1 for clean comparison.
    reset_id_counters()

    kitchen = Kitchen(max_orders=len(orders), num_stoves=num_stoves)

    # Build the chef roster.
    roster = (
        [(f"Grill-{i + 1}",   Station.GRILL)   for i in range(grill_chefs)] +
        [(f"Pasta-{i + 1}",   Station.PASTA)   for i in range(pasta_chefs)] +
        [(f"Dessert-{i + 1}", Station.DESSERT) for i in range(dessert_chefs)]
    )
    chefs = [Chef(name, station, kitchen) for name, station in roster]
    # serve_time=0.05 in the benchmark so the single delivery thread isn't
    # the bottleneck — we want to measure cooking parallelism.
    delivery = Delivery(kitchen, serve_time=0.05)
    for t in chefs + [delivery]:
        t.start()

    # Reset the start clock so we measure only the cooking phase.
    kitchen.sim_start_time = time()
    start = time()

    # "Submit all orders" — fast, since the buffer absorbs them.
    for order in orders:
        kitchen.submit_order(order)

    # Wait for the simulation to finish (delivery thread calls _finish()
    # after the last order has been served).
    while kitchen.is_running():
        sleep(0.05)
    # Give threads a moment to fully wind down.
    for t in chefs + [delivery]:
        t.join(timeout=2.0)

    return round(time() - start, 2)


# --- Public API for Flask ----------------------------------------------------
def run_benchmark(num_orders: int = 8,
                  num_stoves: int = 2,
                  grill_chefs: int = 2,
                  pasta_chefs: int = 2,
                  dessert_chefs: int = 1):
    """Run both modes and return a results dict. All counts come from the
    live sidebar config so the speedup chart reflects whatever the user
    is currently experimenting with."""
    orders_seq = build_orders(num_orders)
    t_seq = run_sequential(orders_seq)

    orders_par = build_orders(num_orders)   # rebuild so they're fresh
    t_par = run_parallel(
        orders_par,
        num_stoves=num_stoves,
        grill_chefs=grill_chefs,
        pasta_chefs=pasta_chefs,
        dessert_chefs=dessert_chefs,
    )

    speedup = round(t_seq / t_par, 2) if t_par > 0 else 0.0
    total_chefs = grill_chefs + pasta_chefs + dessert_chefs
    return {
        "num_orders": num_orders,
        "num_stoves": num_stoves,
        "grill_chefs": grill_chefs,
        "pasta_chefs": pasta_chefs,
        "dessert_chefs": dessert_chefs,
        "total_chefs": total_chefs,
        "time_sequential": t_seq,
        "time_parallel": t_par,
        "speedup": speedup,
        "notes": {
            "sequential": "1 worker, processes every dish one after another.",
            "parallel":   f"{total_chefs} chef threads "
                          f"({grill_chefs} grill + {pasta_chefs} pasta + "
                          f"{dessert_chefs} dessert), {num_stoves} stoves "
                          "shared via semaphore.",
        },
    }


if __name__ == "__main__":
    # python benchmark.py           -> one run with the default kitchen
    # python benchmark.py --stoves  -> same orders with 1, 2, 3 and 4 stoves
    import json
    import sys

    if "--stoves" in sys.argv:
        t_seq = run_sequential(build_orders(8))
        print(f"Sequential (1 worker): {t_seq:.2f}s")
        print("stoves | parallel time | speedup")
        for stoves in (1, 2, 3, 4):
            t_par = run_parallel(build_orders(8), num_stoves=stoves)
            print(f"{stoves:>6} | {t_par:>12.2f}s | {t_seq / t_par:.2f}x")
    else:
        print(json.dumps(run_benchmark(), indent=2))
