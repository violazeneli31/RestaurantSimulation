"""
Chef thread: the *consumer* in the producer-consumer model.

Every chef belongs to a single Station and pulls items off that station's
queue. To cook a grill or pasta item, the chef must first acquire a stove
(a shared semaphore with limited permits) — this is where chefs of
different specializations contend for the same physical resource.
"""

import threading
from queue import Empty
from time import sleep

from models import Station


class Chef(threading.Thread):
    def __init__(self, name, station: Station, kitchen):
        super().__init__(daemon=True, name=name)
        self.chef_name = name
        self.station = station
        self.kitchen = kitchen

    def run(self):
        q = self.kitchen.station_queues[self.station]
        self.kitchen.set_chef_status(self.chef_name, self.station, "idle")
        self.kitchen.log("CHEF_ON",
                         f"{self.chef_name} ({self.station.value}) on shift")

        while True:
            # Wait for an item. The timeout lets the chef notice that the
            # simulation has ended even if no shutdown sentinel reaches it.
            try:
                order, item = q.get(timeout=0.3)
            except Empty:
                if not self.kitchen.is_running():
                    self.kitchen.log("CHEF_OFF",
                                     f"{self.chef_name} clocked out")
                    return
                continue
            if item is self.kitchen.SHUTDOWN or not self.kitchen.is_running():
                self.kitchen.log("CHEF_OFF",
                                 f"{self.chef_name} clocked out")
                return

            # Grill and pasta both need a hot stove; dessert does not.
            need_stove = self.station in (Station.GRILL, Station.PASTA)

            if need_stove:
                self.kitchen.set_chef_status(
                    self.chef_name, self.station,
                    f"waiting for stove ({item.name})",
                )
                self.kitchen.log("WAIT_STOVE",
                                 f"{self.chef_name} waiting for a stove "
                                 f"(needs to cook {item.name})")
                self.kitchen.stoves.acquire()
                if not self.kitchen.is_running():   # stopped while waiting
                    self.kitchen.stoves.release()
                    self.kitchen.log("CHEF_OFF", f"{self.chef_name} clocked out")
                    return
                self.kitchen.log("GOT_STOVE",
                                 f"{self.chef_name} acquired a stove")

            try:
                self.kitchen.mark_item_cooking(order, item, self.chef_name)
                self.kitchen.set_chef_status(
                    self.chef_name, self.station,
                    f"cooking {item.name} for table {order.table}",
                )
                self.kitchen.log("COOK",
                                 f"{self.chef_name} cooking '{item.name}' "
                                 f"for order #{order.order_id} ({item.cook_time}s)")
                # The actual "work" — modeled as a sleep.
                sleep(item.cook_time)
                self.kitchen.mark_item_ready(order, item)
                self.kitchen.log("ITEM_DONE",
                                 f"{self.chef_name} finished '{item.name}'")
            finally:
                if need_stove:
                    self.kitchen.stoves.release()
                    self.kitchen.log("FREE_STOVE",
                                     f"{self.chef_name} released the stove")

            self.kitchen.set_chef_status(self.chef_name, self.station, "idle")
