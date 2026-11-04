"""Shared typed agenda request and result, independent of transports and batch plans."""

from datetime import datetime

from schedule_core.contracts import Contract
from schedule_core.items import ItemDetail


class AgendaRequest(Contract):
    start: datetime | None = None
    end: datetime | None = None
    date: str | None = None
    today: bool = False
    week: bool = False
    title: str | None = None
    calendar: str | None = None
    timezone: str | None = None


class ItemOccurrence(ItemDetail):
    occurrence_start: datetime
