"""
Application entry point.

Builds a Kitchen, starts the waiter / chef / delivery threads, and serves
two Flask pages:

  /              — live dashboard of the running simulation. The simulation
                    auto-terminates after MAX_ORDERS are served.
  /benchmark     — sequential-vs-parallel speedup page (computed on demand).

The sidebar in the dashboard posts a JSON config to /api/restart so that
*every* knob — number of waiters, chefs per station, stoves, max orders,
think-time distribution, randomized cook times — can be changed live
without editing source code.

Run:
    python app.py        # macOS / Linux
    python3 app.py
"""

import threading as _threading
from threading import Thread

from flask import Flask, jsonify, render_template, request

from benchmark import run_benchmark
from chef import Chef
from delivery import Delivery
from kitchen import Kitchen
from models import Station
from waiter import Waiter


# ---- Default simulation configuration ---------------------------------------
# These ship with the app; the sidebar can override any of them.
DEFAULT_CONFIG = {
    "num_waiters": 2,
    "grill_chefs": 2,
    "pasta_chefs": 2,
    "dessert_chefs": 1,
    "num_stoves": 2,
    "max_orders": 12,
    "queue_capacity": 10,
    "serve_time": 1.5,
    "think_time_min": 0.8,
    "think_time_max": 2.0,
    "order_size_min": 1,
    "order_size_max": 3,
    "random_times": False,   # exponential variation on cook times
}


# Holder for the currently-running simulation. Wrapped in a lock so the
# /api/restart route can swap it out safely.
sim_state = {
    "kitchen": None,
    "config": dict(DEFAULT_CONFIG),
    "lock": _threading.Lock(),
}


def _clean_config(raw: dict) -> dict:
    """Coerce / clamp user-supplied values so a malformed POST can never
    crash the simulation."""
    cfg = dict(DEFAULT_CONFIG)
    if not raw:
        return cfg
    int_keys = ("num_waiters", "grill_chefs", "pasta_chefs", "dessert_chefs",
                "num_stoves", "max_orders", "queue_capacity",
                "order_size_min", "order_size_max")
    float_keys = ("serve_time", "think_time_min", "think_time_max")
    for k in int_keys:
        if k in raw:
            try:
                cfg[k] = max(0, int(raw[k]))
            except (TypeError, ValueError):
                pass
    for k in float_keys:
        if k in raw:
            try:
                cfg[k] = max(0.0, float(raw[k]))
            except (TypeError, ValueError):
                pass
    if "random_times" in raw:
        cfg["random_times"] = bool(raw["random_times"])

    # Sanity clamps so the sim is always runnable.
    cfg["num_stoves"] = max(1, cfg["num_stoves"])
    cfg["max_orders"] = max(1, cfg["max_orders"])
    cfg["queue_capacity"] = max(1, cfg["queue_capacity"])
    cfg["num_waiters"] = max(1, cfg["num_waiters"])
    # Every station needs at least one chef: waiters order from the whole
    # menu, so a station with no chef would leave orders unfinished forever.
    for k in ("grill_chefs", "pasta_chefs", "dessert_chefs"):
        cfg[k] = max(1, cfg[k])
    if cfg["order_size_min"] < 1:
        cfg["order_size_min"] = 1
    if cfg["order_size_max"] < cfg["order_size_min"]:
        cfg["order_size_max"] = cfg["order_size_min"]
    if cfg["think_time_max"] < cfg["think_time_min"]:
        cfg["think_time_max"] = cfg["think_time_min"]
    return cfg


def fresh_simulation(cfg: dict = None):
    """Tear down the current simulation (if any) and start a new one."""
    cfg = _clean_config(cfg) if cfg is not None else dict(DEFAULT_CONFIG)
    with sim_state["lock"]:
        old = sim_state["kitchen"]
        # Force any still-running threads to wind down. _finish() is
        # idempotent so this is safe even if the sim already ended.
        if old is not None and old.is_running():
            old._finish()

        kitchen = Kitchen(
            max_orders=cfg["max_orders"],
            num_stoves=cfg["num_stoves"],
            queue_capacity=cfg["queue_capacity"],
        )

        waiters = [
            Waiter(
                f"Waiter-{i + 1}", kitchen,
                think_time=(cfg["think_time_min"], cfg["think_time_max"]),
                order_size=(cfg["order_size_min"], cfg["order_size_max"]),
                random_times=cfg["random_times"],
            )
            for i in range(cfg["num_waiters"])
        ]
        roster = (
            [(f"Grill-{i + 1}",   Station.GRILL)
             for i in range(cfg["grill_chefs"])] +
            [(f"Pasta-{i + 1}",   Station.PASTA)
             for i in range(cfg["pasta_chefs"])] +
            [(f"Dessert-{i + 1}", Station.DESSERT)
             for i in range(cfg["dessert_chefs"])]
        )
        chefs = [Chef(name, station, kitchen)
                 for name, station in roster]
        delivery = Delivery(kitchen, serve_time=cfg["serve_time"])

        for t in waiters + chefs + [delivery]:
            t.start()

        kitchen.log(
            "INIT",
            f"Kitchen open — {cfg['num_waiters']} waiters, "
            f"{len(roster)} chefs "
            f"({cfg['grill_chefs']}G/{cfg['pasta_chefs']}P/"
            f"{cfg['dessert_chefs']}D), "
            f"{cfg['num_stoves']} stoves, "
            f"target {cfg['max_orders']} orders"
            + (", randomized service times" if cfg["random_times"] else "")
        )
        sim_state["kitchen"] = kitchen
        sim_state["config"] = cfg


def build_app():
    """Wire up the kitchen, start all worker threads, attach Flask routes."""
    app = Flask(__name__)
    fresh_simulation()        # initial run

    # ---- Routes -----------------------------------------------------------
    @app.route("/")
    def dashboard():
        return render_template("dashboard.html")

    @app.route("/api/state")
    def state():
        return jsonify(sim_state["kitchen"].snapshot())

    @app.route("/api/config")
    def get_config():
        with sim_state["lock"]:
            return jsonify(dict(sim_state["config"]))

    @app.route("/api/restart", methods=["POST"])
    def restart():
        cfg = request.get_json(silent=True) or {}
        fresh_simulation(cfg)
        with sim_state["lock"]:
            return jsonify({"ok": True, "config": dict(sim_state["config"])})

    # Benchmark page. The actual benchmark is computed on demand to avoid
    # delaying app startup.
    bench_state = {"status": "idle", "result": None}
    bench_lock = _threading.Lock()

    def _run_bench(params):
        try:
            result = run_benchmark(**params)
            with bench_lock:
                bench_state["status"] = "done"
                bench_state["result"] = result
        except Exception as e:
            with bench_lock:
                bench_state["status"] = "error"
                bench_state["result"] = {"error": str(e)}

    @app.route("/benchmark")
    def benchmark_page():
        return render_template("benchmark.html")

    @app.route("/api/benchmark/run", methods=["POST"])
    def benchmark_run():
        # Build benchmark parameters from (1) the live simulation config so
        # the chart matches the sidebar, then (2) any explicit overrides
        # POSTed by the benchmark page form.
        with sim_state["lock"]:
            cfg = dict(sim_state["config"])
        body = request.get_json(silent=True) or {}
        def _pick(key, default):
            v = body.get(key, default)
            try:
                return max(1, int(v))
            except (TypeError, ValueError):
                return default
        params = {
            "num_orders":    _pick("num_orders",    cfg["max_orders"]),
            "num_stoves":    _pick("num_stoves",    cfg["num_stoves"]),
            "grill_chefs":   _pick("grill_chefs",   cfg["grill_chefs"]),
            "pasta_chefs":   _pick("pasta_chefs",   cfg["pasta_chefs"]),
            "dessert_chefs": _pick("dessert_chefs", cfg["dessert_chefs"]),
        }
        with bench_lock:
            if bench_state["status"] == "running":
                return jsonify({"status": "running"})
            bench_state["status"] = "running"
            bench_state["result"] = None
        Thread(target=_run_bench, args=(params,), daemon=True).start()
        return jsonify({"status": "running", "params": params})

    @app.route("/api/benchmark/state")
    def benchmark_state():
        with bench_lock:
            return jsonify({"status": bench_state["status"],
                            "result": bench_state["result"]})

    return app


if __name__ == "__main__":
    application = build_app()
    # use_reloader=False avoids spawning two sets of background threads.
    application.run(host="127.0.0.1", port=5000,
                    debug=False, use_reloader=False)
