from typing import List

from ...context import ctx
from ...server.models import ConfigUpdateRequest
from .config import list_config_sections, update_config


class AdminService:
    def soft_restart(self):
        ctx.destroy()
        ctx.setup()
        ctx.sources.ensure_load()
        ctx.scheduler.start()
        ctx.mail.start()

    def config_sections(self):
        return list_config_sections()

    def refresh_configs(self) -> None:
        """Drop the cached sections after a change made outside the settings page.

        The settings list carries values, so anything that writes config (the
        translator key pool, a key check) has to invalidate it or the page keeps
        reporting the state it was first read in.
        """
        list_config_sections.cache_clear()

    def update_config(self, body: List[ConfigUpdateRequest]):
        for change in body:
            update_config(change.section, change.key, change.value, dry_run=True)
        for change in body:
            update_config(change.section, change.key, change.value)
        list_config_sections.cache_clear()

        # The exits, the solver and the patience settings are read once, when the shared
        # scraper state is built, so without this a crawler setting applies only after a
        # restart while the settings page reports it as live.
        if any(change.section == "crawler" for change in body):
            ctx.scraper.invalidate()

    def update_sources(self) -> int:
        ctx.sources.load(False)
        ctx.sources.update(True)
        return ctx.sources.version
