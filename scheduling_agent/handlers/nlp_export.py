import base64

from scheduling_agent.a2a.task_manager import SchedulingTaskManager
from scheduling_agent.a2a.types import (
    Artifact,
    FileContent,
    InvalidParamsError,
    Message,
    Part,
    Task,
)
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.exporters.base import CalendarExporter
from scheduling_agent.core.schemas import LLMSchedulingParseResult
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.logging_config import get_logger

logger = get_logger(__name__)

async def handle_nlp_export(
    task_id: str,
    nlp_result: LLMSchedulingParseResult,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
) -> Task:
    """Handles the export_calendar intent from NLP result."""
    entities = nlp_result.filters  # Assuming filters contain export params
    logger.info("Handling export_calendar intent", task_id=task_id, entities=entities)
    try:
        # 1. Parse format and date range
        export_format = "ics"  # Default
        if entities and hasattr(entities, "format") and entities.format:
            export_format = entities.format.lower()

        # Use DateUtils to parse the range from NLP date_info
        start_date_str = getattr(nlp_result.date_info, "start_date", None)
        end_date_str = getattr(nlp_result.date_info, "end_date", None)

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

        # 3. Query events from DB
        # Let DatabaseError propagate if it occurs here
        events_to_export = await db_service.get_events_in_range(
            start=range_start_utc, end=range_end_utc
        )

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
