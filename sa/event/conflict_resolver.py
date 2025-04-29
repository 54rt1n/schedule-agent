# sa/event/conflict_resolver.py
# Copyright (C) 2025 Martin Bukowski

"""Conflict resolver for scheduling events."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sa.core.logging_config import get_logger
from sa.db.dao import EventCreate
from sa.db.models import Event
from sa.event.date_utils import DateUtils

logger = get_logger(__name__)


class ConflictResolver:
    """Detects scheduling conflicts and suggests alternatives."""

    # Remove db_service dependency from __init__
    def __init__(self, date_utils: DateUtils):
        self.date_utils = date_utils
        logger.info("ConflictResolver initialized.")

    # Modify signature to accept potential_conflicts
    async def detect_conflicts(
        self, proposed_event: EventCreate, potential_conflicts: list[Event]
    ) -> list[dict[str, Any]]:
        """Detects conflicts between a proposed event and a list of existing events.

        Args:
            proposed_event: An EventCreate model representing the event to check.
            potential_conflicts: A list of existing Event objects (or instances)
                                 that could potentially conflict (pre-fetched by caller).

        Returns:
            A list of conflict dictionaries. Each dictionary contains at least
            the conflicting 'event' object. Returns an empty list if no conflicts.
        """
        logger.debug(f"Detecting conflicts for event '{proposed_event.title}' against {len(potential_conflicts)} potentials.")

        # Determine the query range (need aware UTC) from proposed event
        start_time_utc = self.date_utils.normalize_to_utc(
            proposed_event.start_time, proposed_event.time_zone
        )
        end_time_utc = self.date_utils.normalize_to_utc(
            proposed_event.end_time, proposed_event.time_zone
        )

        conflicts = []
        processed_event_ids = set()  # Keep track of IDs we've added conflicts for

        # Remove the call to db_service.get_events_in_range

        # Iterate through the provided potential conflicts
        for existing_event in potential_conflicts:
            # Ensure existing event times are aware UTC for comparison
            # Assuming potential_conflicts contains instances with correct naive UTC times
            existing_start_utc = existing_event.start_time.replace(tzinfo=UTC)
            existing_end_utc = existing_event.end_time.replace(tzinfo=UTC)

            # Overlap condition: (StartA < EndB) and (EndA > StartB)
            if (start_time_utc < existing_end_utc and
                    end_time_utc > existing_start_utc and
                    existing_event.id not in processed_event_ids):
                logger.info(
                    "Instance conflict detected",
                    proposed_title=proposed_event.title,
                    existing_title=existing_event.title,
                    existing_id=existing_event.id,
                )
                # Ensure the conflicting event added is serializable if needed later
                # (SQLModel instances usually are)
                conflicts.append(
                    {
                        "type": "instance",
                        "event": existing_event, # Store the conflicting Event object
                    }
                )
                processed_event_ids.add(existing_event.id)

        # TODO: Pattern conflict detection needs separate handling or refactoring
        # The original logic re-iterated over potential_instance_conflicts and checked
        # if both proposed and existing were recurring. This might need the *base*
        # definitions of recurring events, not just the instances within the range.
        # The caller (strategy) might need to fetch base recurring events separately
        # if pattern checking is required here. For now, only instance overlap is checked.

        logger.debug(f"Conflict detection complete. Found {len(conflicts)} conflicts.")
        return conflicts

    def _merge_overlapping_periods(
        self, periods: list[tuple[datetime, datetime]]
    ) -> list[tuple[datetime, datetime]]:
        """Merges overlapping or adjacent time periods."""
        if not periods:
            return []
        periods.sort(key=lambda x: x[0])
        merged = []
        if not periods: return merged # Handle empty list after sort
        current_start, current_end = periods[0]
        for next_start, next_end in periods[1:]:
            if next_start <= current_end:
                current_end = max(current_end, next_end)
            else:
                merged.append((current_start, current_end))
                current_start, current_end = next_start, next_end
        merged.append((current_start, current_end))
        return merged

    def _is_pattern_conflict(
        self,
        start_time1: datetime,
        end_time1: datetime,
        rrule1: str,
        start_time2: datetime,
        end_time2: datetime,
        rrule2: str,
        conflict_window_days: int = 180,
        pattern_threshold: int = 3,
    ) -> tuple[bool, int]:
        """Checks if two recurring events have a pattern conflict within a window."""
        # (Implementation remains the same for now, relies on DateUtils)
        logger.debug("Checking for pattern conflict", rrule1=rrule1, rrule2=rrule2)
        st1 = self.date_utils.normalize_to_utc(start_time1)
        et1 = self.date_utils.normalize_to_utc(end_time1)
        st2 = self.date_utils.normalize_to_utc(start_time2)
        et2 = self.date_utils.normalize_to_utc(end_time2)
        duration1 = et1 - st1
        duration2 = et2 - st2
        window_start = max(st1, st2)
        window_end = window_start + timedelta(days=conflict_window_days)
        try:
            # Ensure expand_recurrence can handle base times and rrule string
            # It might need adjustment if it previously relied on full Event objects
            occurrences1 = self.date_utils.expand_recurrence_simple(st1, rrule1, until=window_end)
            occurrences2 = self.date_utils.expand_recurrence_simple(st2, rrule2, until=window_end)
            # Filter occurrences to be within the actual window_start
            occurrences1 = [occ for occ in occurrences1 if occ >= window_start]
            occurrences2 = [occ for occ in occurrences2 if occ >= window_start]
        except ValueError as e:
            logger.warning(f"Failed to expand recurrence for pattern check: {e}")
            return True, 0 # Indicate potential conflict if expansion fails
        except AttributeError:
             logger.warning("DateUtils does not have expand_recurrence_simple method.")
             return True, 0 # Indicate potential conflict if method missing

        intervals2 = set((occ2_start, occ2_start + duration2) for occ2_start in occurrences2)
        if not intervals2: return False, 0
        conflict_count = 0
        for occ1_start in occurrences1:
            occ1_end = occ1_start + duration1
            for int2_start, int2_end in intervals2:
                if occ1_start < int2_end and occ1_end > int2_start:
                    conflict_count += 1
                    # Optional: remove interval from set to avoid recounting

                    break # Move to next occ1
            if conflict_count >= pattern_threshold:
                logger.info("Pattern conflict detected", count=conflict_count, threshold=pattern_threshold)
                return True, conflict_count
        logger.debug(f"Pattern conflict check complete. Found {conflict_count} overlaps.")
        return False, conflict_count

    # --- Helpers for generate_alternative_times ---
    def get_alternative_ranges(
        self,
        proposed_event: EventCreate,
        potential_conflicts: list[Event],
    ) -> list[tuple[datetime, datetime]]:
        """Returns alternative time ranges to search for conflicts."""
        return []
    
    def generate_alternative_times(
        self,
        proposed_event: EventCreate,
        time_ranges: list[tuple[datetime, datetime]],
        current_events: list[Event],
    ) -> list[tuple[datetime, datetime]]:
        """Based on time ranges and current events, returns alternative times."""
        return []

    def _merge_overlapping_periods(
        self, periods: list[tuple[datetime, datetime]]
    ) -> list[tuple[datetime, datetime]]:
        """Merges overlapping or adjacent time periods."""
        if not periods:
            return []
        periods.sort(key=lambda x: x[0])
        merged = []
        if not periods: return merged # Handle empty list after sort
        current_start, current_end = periods[0]
        for next_start, next_end in periods[1:]:
            if next_start <= current_end:
                current_end = max(current_end, next_end)
            else:
                merged.append((current_start, current_end))
                current_start, current_end = next_start, next_end
        merged.append((current_start, current_end))
        return merged

    def _is_pattern_conflict(
        self,
        start_time1: datetime,
        end_time1: datetime,
        rrule1: str,
        start_time2: datetime,
        end_time2: datetime,
        rrule2: str,
        conflict_window_days: int = 180,
        pattern_threshold: int = 3,
    ) -> tuple[bool, int]:
        """Checks if two recurring events have a pattern conflict within a window."""
        # (Implementation remains the same for now, relies on DateUtils)
        logger.debug("Checking for pattern conflict", rrule1=rrule1, rrule2=rrule2)
        st1 = self.date_utils.normalize_to_utc(start_time1)
        et1 = self.date_utils.normalize_to_utc(end_time1)
        st2 = self.date_utils.normalize_to_utc(start_time2)
        et2 = self.date_utils.normalize_to_utc(end_time2)
        duration1 = et1 - st1
        duration2 = et2 - st2
        window_start = max(st1, st2)
        window_end = window_start + timedelta(days=conflict_window_days)
        try:
            # Ensure expand_recurrence can handle base times and rrule string
            # It might need adjustment if it previously relied on full Event objects
            occurrences1 = self.date_utils.expand_recurrence_simple(st1, rrule1, until=window_end)
            occurrences2 = self.date_utils.expand_recurrence_simple(st2, rrule2, until=window_end)
            # Filter occurrences to be within the actual window_start
            occurrences1 = [occ for occ in occurrences1 if occ >= window_start]
            occurrences2 = [occ for occ in occurrences2 if occ >= window_start]
        except ValueError as e:
            logger.warning(f"Failed to expand recurrence for pattern check: {e}")
            return True, 0 # Indicate potential conflict if expansion fails
        except AttributeError:
             logger.warning("DateUtils does not have expand_recurrence_simple method.")
             return True, 0 # Indicate potential conflict if method missing

        intervals2 = set((occ2_start, occ2_start + duration2) for occ2_start in occurrences2)
        if not intervals2: return False, 0
        conflict_count = 0
        for occ1_start in occurrences1:
            occ1_end = occ1_start + duration1
            for int2_start, int2_end in intervals2:
                if occ1_start < int2_end and occ1_end > int2_start:
                    conflict_count += 1
                    # Optional: remove interval from set to avoid recounting

                    break # Move to next occ1
            if conflict_count >= pattern_threshold:
                logger.info("Pattern conflict detected", count=conflict_count, threshold=pattern_threshold)
                return True, conflict_count
        logger.debug(f"Pattern conflict check complete. Found {conflict_count} overlaps.")
        return False, conflict_count 
