# sa/a2a/command/structured_export.py
"""Handles the export_calendar intent from NLP result."""

import base64

from sa.a2a.dto import (
    Artifact,
    FileContent,
    InvalidParamsError,
    Message,
    Part,
    Task,
)
from sa.a2a.task_manager import SchedulingTaskManager
from sa.core.logging_config import get_logger
from sa.db.service import DatabaseService
from sa.event.date_utils import DateUtils
from sa.event.exporters.base import CalendarExporter
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy

logger = get_logger(__name__)

async def handle_structured_export(
    task_id: str,
    message: Message,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
) -> Task:
    """Handles the export_calendar intent from NLP result."""
    logger.info(f"Handling export_calendar intent for task {task_id}")
    try:
        input_data = message.parts[0].data

        # Ensure input_data is a dict before accessing keys
        if not isinstance(input_data, dict):
            raise InvalidParamsError("Invalid data format for export payload.")

        # 1. Parse format and date range
        export_format = input_data.get("format", "ics").lower()

        # Use DateUtils to parse the range from NLP date_info (which is a dict within input_data)
        date_info_dict = input_data.get("date_info", {})
        start_date_str = date_info_dict.get("start_date")
        end_date_str = date_info_dict.get("end_date")

        # Let DateUtils handle parsing, defaults, and errors
        range_start_utc, range_end_utc = date_utils.parse_nlp_date_range(
            start_date_str=start_date_str,
            end_date_str=end_date_str
        )

        logger.info(
            "Determined export date range (UTC)",
            task_id=task_id,
            start=range_start_utc,
            end=range_end_utc,
        )

        # 2. Get the correct exporter
        exporter = exporters.get(export_format)
        if not exporter:
            # This is an invalid parameter from the user/NLP, handle it here
            raise InvalidParamsError(f"Unsupported export format requested: {export_format}")

        # 3. Query events from DB using the strategy
        get_events_strategy = GetEventsInRangeStrategy(
            db_service=db_service, date_utils=date_utils # Pass dependencies
        )
        # Strategy expects a tuple (start_dt, end_dt) as data
        events_to_export = await get_events_strategy.execute(data=(range_start_utc, range_end_utc))

        # 4. Export events
        # Let ExporterError or other Exceptions propagate if they occur here
        exported_data = exporter.export(events_to_export)
        mime_type = exporter.get_mime_type()
        file_name = f"calendar.{export_format}"

        # 5. Base64 encode the result
        encoded_bytes = base64.b64encode(exported_data.encode("utf-8")).decode("utf-8")

        # 6. Create Artifact
        file_content = FileContent(name=file_name, mimeType=mime_type, bytes=encoded_bytes)
        artifact = Artifact(name=file_name, parts=[Part(type="file", file=file_content)])

        # 7. Complete task
        result_message = f"Calendar exported successfully as {file_name}."
        agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
        final_task = await task_manager.complete_task(task_id, agent_message, artifact)
        logger.info("Export task completed successfully", task_id=task_id)
        return final_task

    except InvalidParamsError as ipe:
        # Catch InvalidParamsError specifically to let it propagate clearly
        logger.warning(f"InvalidParamsError during export: {ipe.message}", task_id=task_id)
        raise
    # Let other errors (DatabaseError, ExporterError, etc.) propagate
