# packages/schedule-a2a/src/schedule_a2a/command/structured_export.py
# Copyright (C) 2025 Martin Bukowski

"""Handles the export_calendar intent."""

import base64
from typing import Any

from schedule_a2a.dto import Artifact, FileContent, Task, Part, Message
from schedule_a2a.exceptions import InvalidParamsError, InternalError
from schedule_a2a.task_manager import SchedulingTaskManager
from schedule_core.core.exceptions import ExporterError
from schedule_core.core.logging_config import get_logger
from schedule_core.db.service import DatabaseService
from schedule_core.db.dao import TimeRange
from schedule_core.event.date_utils import DateUtils
from schedule_core.event.exporters.base import CalendarExporter
from schedule_core.db.strategies.get_events_in_range import GetEventsInRangeStrategy

logger = get_logger(__name__)

async def handle_structured_export(
    task_id: str,
    payload: dict[str, Any],
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
) -> Task:
    """Handles structured calendar exports based on the unwrapped payload (dict)."""
    logger.info(f"Handling export_calendar intent for task {task_id}")
    try:
        # 2. Parse format and date range from payload
        export_format = payload.get("format", "ics").lower()
        
        # Get the nested date_info dictionary, default to empty dict if not found
        date_info_payload = payload.get("date_info", {})
        if not isinstance(date_info_payload, dict):
            raise InvalidParamsError("Invalid structure for date_info in payload.")

        # Extract dates from the nested dictionary
        start_date_str = date_info_payload.get("start_date") 
        end_date_str = date_info_payload.get("end_date")

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

        # 3. Get the correct exporter
        exporter = exporters.get(export_format)
        if not exporter:
            raise InvalidParamsError(f"Unsupported export format requested: {export_format}")

        # 4. Query events from DB using the strategy
        get_events_strategy = GetEventsInRangeStrategy(
            db_service=db_service, date_utils=date_utils
        )
        time_range = TimeRange(start_time=range_start_utc, end_time=range_end_utc)
        events_to_export = await get_events_strategy.execute(data=time_range)

        # 5. Export events
        exported_data = exporter.export(events_to_export)
        mime_type = exporter.get_mime_type()
        file_name = f"calendar.{export_format}"

        # 6. Base64 encode the result
        encoded_bytes = base64.b64encode(exported_data.encode("utf-8")).decode("utf-8")

        # 7. Create Artifact
        file_content = FileContent(name=file_name, mimeType=mime_type, bytes=encoded_bytes)
        artifact = Artifact(name=file_name, parts=[Part(type="file", file=file_content)])

        # 8. Complete task
        result_message = f"Calendar exported successfully as {file_name}."
        agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
        final_task = await task_manager.complete_task(task_id, agent_message, artifact)
        logger.info("Export task completed successfully", task_id=task_id)
        return final_task

    except ExporterError as error:
        raise InternalError(message=error.message, data=error.data) from error
    except InvalidParamsError as ipe:
        logger.warning(f"InvalidParamsError during export: {ipe.message}", task_id=task_id)
        raise
    except ValueError as ve: # Catch potential errors from date parsing
        logger.warning(f"ValueError during export processing (likely date parsing): {ve}", task_id=task_id)
        raise InvalidParamsError(f"Invalid date format provided for export: {ve}") from ve
    except Exception as e:
        logger.exception(f"Unexpected error processing structured export for task {task_id}: {e}")
        raise InternalError(message=f"An unexpected error occurred during export: {e}") from e
