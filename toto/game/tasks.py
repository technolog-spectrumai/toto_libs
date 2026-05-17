try:
    from celery import shared_task, group
except ImportError:  # Allows app import without Celery installed.
    shared_task = None
    group = None

from .models import Planet
from .services import increment_global_tick, run_planet_tick


if shared_task:
    @shared_task
    def run_planet_tick_task(planet_id: int, tick_number: int):
        run_planet_tick(planet_id)
        return {"planet_id": planet_id, "tick": tick_number, "status": "ok"}


    @shared_task
    def run_global_game_tick():
        tick_number = increment_global_tick()
        planet_ids = list(Planet.objects.filter(owner__isnull=False).values_list("id", flat=True))
        if not planet_ids:
            return {"tick": tick_number, "planets": 0}
        group(run_planet_tick_task.s(planet_id, tick_number) for planet_id in planet_ids).apply_async()
        return {"tick": tick_number, "planets": len(planet_ids)}
else:
    # TODO: Install Celery and configure beat for production ticks.
    pass
