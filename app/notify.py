"""Ilmoitusspämmin esto. Ajetaan tapahtumasilmukassa (FastAPI BackgroundTasks + async), joten tila ei tarvitse lukkoja.

Tilamuutokset: odotetaan DEBOUNCE sekuntia ja lähetetään vain viimeisin tila. Samasta keittimestä enintään
yksi ilmoitus MIN_INTERVAL sekunnissa, eikä samaa tilaa lähetetä kahdesti peräkkäin. Viimeisin tila menee
silti aina perille (odotus vain pitenee).

Toiveet: enintään yksi ilmoitus WISH_INTERVAL sekunnissa per keitin. Toiveiden määrä kertyy seuraavaan.
"""
import asyncio
import os
import time

from starlette.concurrency import run_in_threadpool

from . import push

DEBOUNCE = float(os.environ.get("NOTIFY_DEBOUNCE", 15))
MIN_INTERVAL = float(os.environ.get("NOTIFY_MIN_INTERVAL", 120))
WISH_INTERVAL = float(os.environ.get("NOTIFY_WISH_INTERVAL", 15 * 60))

_pending: dict[str, dict] = {}                       # keitin -> viimeisin odottava tila
_last_sent: dict[str, tuple[str, float]] = {}         # keitin -> (tila, aika)
_last_wish: dict[str, float] = {}                     # keitin -> aika
_runners: set[asyncio.Task] = set()


async def status_changed(machine_id: str, status: str, title: str, body, skip_device: str | None) -> None:
    """body: funktio kieli -> teksti."""
    already_waiting = machine_id in _pending
    _pending[machine_id] = {"status": status, "title": title, "body": body, "skip": skip_device}
    if not already_waiting:
        task = asyncio.create_task(_run(machine_id))
        _runners.add(task)
        task.add_done_callback(_runners.discard)


async def _run(machine_id: str) -> None:
    _, last_ts = _last_sent.get(machine_id, (None, 0.0))
    await asyncio.sleep(max(DEBOUNCE, last_ts + MIN_INTERVAL - time.time()))
    p = _pending.pop(machine_id, None)
    if not p:
        return
    last_status, _ = _last_sent.get(machine_id, (None, 0.0))
    if p["status"] == last_status:
        return  # esim. "on" -> "loppu" -> "on": seuraajien kannalta mikään ei muuttunut
    _last_sent[machine_id] = (p["status"], time.time())
    await run_in_threadpool(push.notify_machine, machine_id, p["title"], p["body"], p["skip"])


async def wished(machine_id: str, title: str, body, skip_device: str | None) -> None:
    now = time.time()
    if now - _last_wish.get(machine_id, 0.0) < WISH_INTERVAL:
        return
    _last_wish[machine_id] = now
    await run_in_threadpool(push.notify_machine, machine_id, title, body, skip_device)
