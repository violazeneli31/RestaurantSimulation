# Viola's Cuisine – Restaurant Kitchen Simulation

A restaurant kitchen simulated with Python threads, with a live web dashboard that shows every thread at work.

Project for the **Parallel Programming** course at Epoka University (Spring 2026).

## How it works

Every person in the kitchen is a separate thread:

| Role | Threads | What it does |
|---|---|---|
| **Waiters** | 2 | Create random orders and send each dish to its station |
| **Chefs** | 5 (2 grill, 2 pasta, 1 dessert) | Take dishes from their station's queue and cook them |
| **Delivery** | 1 | Waits until a whole order is ready, then serves it |

The simulation stops after 12 orders are served. All of these numbers can be changed from the dashboard.

## The 4 synchronization tools

| Tool | What it's used for |
|---|---|
| **Queue** | One queue per station between waiters and chefs (producer–consumer). When a queue is full, waiters wait. |
| **Semaphore** | There are only 2 stoves. Grill and pasta chefs must get a stove before cooking. |
| **Lock** | Protects the shared order list and statistics, so two threads never change them at the same time. |
| **Condition variable** | Delivery sleeps until a chef finishes the last dish of an order, instead of checking over and over. |

## Results

The same 8 orders, cooked by 1 worker vs. the full kitchen (2 grill, 2 pasta, 1 dessert chef):

| | Time | Speedup |
|---|---|---|
| 1 worker (sequential) | 15.40 s | 1× |
| Kitchen with 1 stove | 12.00 s | 1.28× |
| Kitchen with 2 stoves | 6.23 s | **2.47×** |
| Kitchen with 3 stoves | 4.47 s | 3.45× |
| Kitchen with 4 stoves | 3.95 s | 3.90× |

- **The stoves are the bottleneck.** 4 chefs need a stove, but there are only 2, so adding stoves helps the most.
- **Why threads help here:** cooking is simulated with `sleep`, so threads spend most of their time waiting, and they can all wait at the same time. For heavy calculations, Python threads would not run faster because of Python's GIL.

## Project structure

| File / folder | What's inside | How to use it |
|---|---|---|
| `restaurant_simulation/app.py` | Starts the simulation and the web dashboard | `python app.py`, then open http://127.0.0.1:5000 |
| `kitchen.py` | The queues, semaphore, lock and condition variable | The shared kitchen that all threads use |
| `waiter.py`, `chef.py`, `delivery.py` | The three kinds of threads | Started by `app.py` |
| `models.py` | Orders, dishes and the menu | Change dishes or cook times here |
| `benchmark.py` | Speed test: 1 worker vs. the kitchen | `python benchmark.py` (add `--stoves` for the table above) |
| `templates/` | The dashboard and benchmark web pages | Opened by `app.py` |
| `tests/` | Checks that every order is served, the stove limit is respected and all threads stop | `pytest` |
| `presentation/` | Project slides (PDF) | Open the PDF |

## How to run

```
cd restaurant_simulation
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 in your browser.

- **Dashboard:** change the sliders and press **Apply & Restart**.
- **Speed test:** open http://127.0.0.1:5000/benchmark and press **Run benchmark**.
- Press `Ctrl+C` in the terminal to stop.

## Built with

Python (`threading`, `queue`) · Flask · HTML/CSS/JavaScript

## Author

Viola Zeneli
