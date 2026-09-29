from arcms.core.jobs import periodic

from .services import poll_due, purge_old


@periodic("wires_poll", 60)
def wires_poll() -> None:
    poll_due()


@periodic("wires_purge", 6 * 3600)
def wires_purge() -> None:
    purge_old()
