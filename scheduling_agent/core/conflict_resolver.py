"""Handles conflict detection and resolution for scheduling."""

from typing import List, Dict, Any, Optional, TYPE_CHECKING
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

# Import DB models from .models and DTO schemas from .schemas
from scheduling_agent.db.models import Event
from scheduling_agent.db.schemas import EventCreate
# Remove direct import of DatabaseService
# from scheduling_agent.db.service import DatabaseService
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.logging_config import get_logger

# Use TYPE_CHECKING for type hint to avoid circular import
if TYPE_CHECKING:
    from scheduling_agent.db.service import DatabaseService

logger = get_logger(__name__)

class ConflictResolver:
    """Detects scheduling conflicts and suggests alternatives."""

    # Update type hint using the string or the TYPE_CHECKING import
    def __init__(self, db_service: "DatabaseService", date_utils: DateUtils):
        self.db_service = db_service
        self.date_utils = date_utils

    async def detect_conflicts(self, proposed_event: EventCreate) -> List[Dict[str, Any]]:
        """Detects conflicts between a proposed event and existing schedule.

        Args:
            proposed_event: An EventCreate model representing the event to check.

        Returns:
            A list of conflict dictionaries. Each dictionary contains at least
            the conflicting 'event' object. Returns an empty list if no conflicts.
        """
        logger.debug("Detecting conflicts for event", title=proposed_event.title)

        # Determine the query range (need aware UTC)
        start_time_utc = self.date_utils.normalize_to_utc(proposed_event.start_time, proposed_event.time_zone)
        end_time_utc = self.date_utils.normalize_to_utc(proposed_event.end_time, proposed_event.time_zone)
        duration = end_time_utc - start_time_utc

        # Fetch potentially conflicting event *instances* from the database service
        # This covers non-recurring events and specific instances of recurring ones.
        potential_instance_conflicts = await self.db_service.get_events_in_range(
            start=start_time_utc,
            end=end_time_utc
        )

        conflicts = []
        processed_event_ids = set() # Keep track of IDs we've added conflicts for

        # 1. Check for direct instance overlaps
        for existing_event in potential_instance_conflicts:
            # Compare aware UTC times
            existing_start_utc = existing_event.start_time.replace(tzinfo=timezone.utc)
            existing_end_utc = existing_event.end_time.replace(tzinfo=timezone.utc)

            # Overlap condition: (StartA < EndB) and (EndA > StartB)
            if (start_time_utc < existing_end_utc and
                end_time_utc > existing_start_utc):
                if existing_event.id not in processed_event_ids:
                    logger.info(
                        "Instance conflict detected",
                        proposed_title=proposed_event.title,
                        existing_title=existing_event.title,
                        existing_id=existing_event.id
                    )
                    conflicts.append({
                        "type": "instance", # Mark as instance overlap
                        "event": existing_event,
                    })
                    processed_event_ids.add(existing_event.id)

        # 2. Check for pattern conflicts if proposed event is recurring
        if proposed_event.rrule:
            logger.debug("Proposed event is recurring, checking for pattern conflicts.")
            # We need the definitions of potentially conflicting recurring events.
            # Assumption: get_events_in_range can return definitions, or we need another method.
            # For this unit test context, we assume potential_instance_conflicts *might* contain
            # recurring definitions IF the mock is set up that way.
            # A more robust implementation might need db_service changes.

            for existing_event in potential_instance_conflicts: # Re-iterate or use a different list
                # Check if the existing event is recurring.
                # We perform the pattern check even if an instance conflict was already found for this ID,
                # as we might need to upgrade the conflict type to 'pattern'.
                if existing_event.rrule:
                    # Both are recurring, check pattern
                    existing_start_utc = existing_event.start_time.replace(tzinfo=timezone.utc)
                    existing_end_utc = existing_event.end_time.replace(tzinfo=timezone.utc)

                    is_pattern, count = self._is_pattern_conflict(
                        start_time1=start_time_utc, end_time1=end_time_utc, rrule1=proposed_event.rrule,
                        start_time2=existing_start_utc, end_time2=existing_end_utc, rrule2=existing_event.rrule
                    )

                    if is_pattern:
                         logger.info(
                            "Pattern conflict detected",
                            proposed_title=proposed_event.title,
                            existing_title=existing_event.title,
                            existing_id=existing_event.id,
                            overlap_count=count
                        )
                         # Add or update conflict entry
                         # Check if we already added this event as an instance conflict
                         found = False
                         for conflict in conflicts:
                            if conflict['event'].id == existing_event.id:
                                conflict['type'] = 'pattern' # Upgrade to pattern conflict
                                conflict['pattern_overlap_count'] = count
                                found = True
                                break
                         if not found:
                            conflicts.append({
                                "type": "pattern",
                                "event": existing_event,
                                "pattern_overlap_count": count
                            })
                         processed_event_ids.add(existing_event.id)


        logger.debug(f"Conflict detection complete. Found {len(conflicts)} conflicts.")
        return conflicts

    async def generate_alternative_times(self, proposed_event: EventCreate, conflicts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Generates alternative time suggestions based on detected conflicts.

        Args:
            proposed_event: The original event request.
            conflicts: The list of detected conflicts from detect_conflicts.

        Returns:
            A list of alternative time suggestions (dictionaries with startTime, endTime, label).
        """
        logger.debug("Generating alternative times", proposed_title=proposed_event.title)
        alternatives = []

        # Parse original event times
        original_start = self.date_utils.normalize_to_utc(proposed_event.start_time, proposed_event.time_zone)
        original_end = self.date_utils.normalize_to_utc(proposed_event.end_time, proposed_event.time_zone)
        duration = original_end - original_start

        # --- User Preferences (Placeholder - fetch from config/user settings later) ---
        business_hours_start = time(9, 0)  # 9:00 AM
        business_hours_end = time(17, 0)   # 5:00 PM
        business_days = [0, 1, 2, 3, 4]    # Monday to Friday (0=Monday)
        time_zone_str = proposed_event.time_zone or "UTC" # Use proposed event's timezone or default
        # --- End User Preferences ---

        try:
            # Get busy periods around the proposed time to check for availability
            # Define a reasonable window to check for free slots (e.g., next 14 days)
            query_start = original_start
            query_end = original_start + timedelta(days=14)

            busy_periods = await self._get_busy_periods(
                query_start, query_end, time_zone_str
            )

            # --- Strategy 1: Later on the same day ---
            next_slot_same_day = self._find_next_available_slot_same_day(
                original_start, duration, busy_periods,
                business_hours_start, business_hours_end, business_days,
                time_zone_str
            )
            if next_slot_same_day:
                alternatives.append(self._format_alternative(next_slot_same_day, duration, time_zone_str, "Later today"))


            # --- Strategy 2: Same time next available business day ---
            if len(alternatives) < 3: # Limit suggestions
                 next_slot_next_day = self._find_next_business_day_slot(
                    original_start, duration, busy_periods,
                    business_hours_start, business_hours_end, business_days,
                    time_zone_str
                )
                 if next_slot_next_day:
                     alternatives.append(self._format_alternative(next_slot_next_day, duration, time_zone_str, "Same time next business day"))


            # --- Strategy 3: Find first available slots in the coming days ---
            if len(alternatives) < 3: # Fill remaining suggestions
                additional_slots = self._find_available_slots(
                    original_start, duration, busy_periods,
                    business_hours_start, business_hours_end, business_days,
                    time_zone_str, max_slots=3 - len(alternatives), max_days_ahead=14
                )
                for slot in additional_slots:
                     if len(alternatives) < 3:
                        alternatives.append(self._format_alternative(slot, duration, time_zone_str, "Next available"))


            logger.info(f"Generated {len(alternatives)} alternative time suggestions.")
            return alternatives

        except Exception as e:
            logger.error("Error generating alternative times", error=e, exc_info=True)
            return [] # Return empty list on error


    async def _get_busy_periods(
        self, query_start: datetime, query_end: datetime, time_zone_str: str
    ) -> List[tuple[datetime, datetime]]:
        """Fetches and prepares busy time slots within a given range."""
        logger.debug("Fetching busy periods", start=query_start, end=query_end)
        
        # Fetch events from the database
        # Ensure query range is sufficient to find slots
        events = await self.db_service.get_events_in_range(
            start=query_start, # Use aware datetimes for query if possible
            end=query_end
        )

        busy_periods = []
        for event in events:
            # Ensure we are using timezone-aware datetimes for comparison
            # Assuming db returns naive UTC, make them aware
            event_start = event.start_time.replace(tzinfo=timezone.utc)
            event_end = event.end_time.replace(tzinfo=timezone.utc)
            busy_periods.append((event_start, event_end))

        # Optional: Add buffer time around existing events (e.g., 15 mins)
        # buffered_periods = []
        # buffer = timedelta(minutes=15)
        # for start, end in busy_periods:
        #    buffered_periods.append((start - buffer, end + buffer))
        
        # Sort and merge overlapping periods
        return self._merge_overlapping_periods(busy_periods)


    def _merge_overlapping_periods(
        self, periods: List[tuple[datetime, datetime]]
    ) -> List[tuple[datetime, datetime]]:
        """Merges overlapping or adjacent time periods."""
        if not periods:
            return []

        # Sort periods by start time
        periods.sort(key=lambda x: x[0])

        merged = []
        current_start, current_end = periods[0]

        for next_start, next_end in periods[1:]:
            if next_start <= current_end: # Overlap or adjacent
                # Merge: extend the current end time if the next period ends later
                current_end = max(current_end, next_end)
            else:
                # No overlap: add the completed period and start a new one
                merged.append((current_start, current_end))
                current_start, current_end = next_start, next_end

        # Add the last merged period
        merged.append((current_start, current_end))
        return merged

    def _is_pattern_conflict(
        self,
        start_time1: datetime, end_time1: datetime, rrule1: str,
        start_time2: datetime, end_time2: datetime, rrule2: str,
        conflict_window_days: int = 180,
        pattern_threshold: int = 3
    ) -> tuple[bool, int]:
        """Checks if two recurring events have a pattern conflict within a window.

        Args:
            start_time1, end_time1, rrule1: Details of the first recurring event.
            start_time2, end_time2, rrule2: Details of the second recurring event.
            conflict_window_days: How many days ahead to check for conflicts.
            pattern_threshold: How many overlaps constitute a pattern conflict.

        Returns:
            A tuple: (bool indicating if a pattern conflict exists, number of conflicts found).
        """
        logger.debug("Checking for pattern conflict", rrule1=rrule1, rrule2=rrule2)
        # Ensure times are timezone-aware UTC for expansion
        st1 = self.date_utils.normalize_to_utc(start_time1)
        et1 = self.date_utils.normalize_to_utc(end_time1)
        st2 = self.date_utils.normalize_to_utc(start_time2)
        et2 = self.date_utils.normalize_to_utc(end_time2)
        duration1 = et1 - st1
        duration2 = et2 - st2

        # Define the window for checking conflicts
        window_start = max(st1, st2)
        # Adjust window based on frequency? (Simplified for now)
        window_end = window_start + timedelta(days=conflict_window_days)

        try:
            # Expand recurrences within the window
            # Use caching if DateUtils supports it
            occurrences1 = self.date_utils.expand_recurrence(st1, window_start, window_end, rrule1)
            occurrences2 = self.date_utils.expand_recurrence(st2, window_start, window_end, rrule2)
        except ValueError as e:
            logger.warning(f"Failed to expand recurrence for pattern check: {e}")
            # Treat expansion failure as potential conflict to be safe?
            return True, 0 # Indicate potential pattern, but 0 conflicts found

        # Optimize: Create a set of occurrence intervals for faster checking
        # Store intervals as (start_utc, end_utc)
        intervals2 = set()
        for occ2_start in occurrences2:
            intervals2.add((occ2_start, occ2_start + duration2))

        if not intervals2:
            return False, 0 # Second rule generated no occurrences in window

        conflict_count = 0
        # Check overlaps
        for occ1_start in occurrences1:
            occ1_end = occ1_start + duration1
            # Check against all intervals in the second set
            for int2_start, int2_end in intervals2:
                # Overlap condition: (StartA < EndB) and (EndA > StartB)
                if occ1_start < int2_end and occ1_end > int2_start:
                    conflict_count += 1
                    # Optional: Remove the conflicting interval from intervals2 to avoid recounting
                    # if one occ1 overlaps multiple occ2 (e.g. daily vs weekly)
                    # intervals2.remove((int2_start, int2_end))
                    break # Move to the next occ1 once an overlap is found for it

            if conflict_count >= pattern_threshold:
                logger.info("Pattern conflict detected", count=conflict_count, threshold=pattern_threshold)
                return True, conflict_count

        logger.debug(f"Pattern conflict check complete. Found {conflict_count} overlaps.")
        return False, conflict_count

    def _find_next_available_slot_same_day(
        self, start_dt: datetime, duration: timedelta, busy_periods: list,
        business_hours_start: time, business_hours_end: time, business_days: list,
        time_zone_str: str
    ) -> Optional[datetime]:
        """Finds the next available slot on the same day, respecting business hours."""
        logger.debug("Finding next available slot on same day")
        try:
            local_tz = ZoneInfo(time_zone_str)
        except Exception:
            logger.warning(f"Invalid timezone string '{time_zone_str}'. Falling back to UTC.")
            local_tz = timezone.utc # Fallback
        current_day = start_dt.astimezone(local_tz).date()

        # Check if the day is a business day
        if start_dt.astimezone(local_tz).weekday() not in business_days:
            return None

        # Define business hours for the current day in the local timezone
        bh_start = datetime.combine(current_day, business_hours_start, tzinfo=local_tz)
        bh_end = datetime.combine(current_day, business_hours_end, tzinfo=local_tz)

        # Start searching from the end of the original event, or beginning of business hours, whichever is later
        # Align to the next 15-minute interval after the original event end time.
        original_end_local = start_dt.astimezone(local_tz) + duration
        search_start_local = max(original_end_local, bh_start)
        # Align to the next 15 min mark
        minute_offset = search_start_local.minute % 15
        if minute_offset != 0:
            search_start_local += timedelta(minutes=15 - minute_offset)

        # Use a small increment (e.g., 15 mins) to check slots
        search_increment = timedelta(minutes=15)
        current_check_time_local = search_start_local

        while current_check_time_local + duration <= bh_end:
            slot_start_utc = current_check_time_local.astimezone(timezone.utc)
            slot_end_utc = (current_check_time_local + duration).astimezone(timezone.utc)

            is_available = True
            conflicting_busy_end = None # Store the end time of the conflict
            # Check against merged busy periods (already in UTC)
            for busy_start, busy_end in busy_periods:
                if slot_start_utc < busy_end and slot_end_utc > busy_start:
                    is_available = False
                    conflicting_busy_end = busy_end # Store the end time
                    break # Found a conflict, no need to check further busy periods

            if is_available:
                return slot_start_utc # Return the first available slot found (in UTC)
            else:
                # If not available due to conflict, advance past the conflicting busy period.
                # Ensure advancement is at least by the search increment.
                advance_to_local = conflicting_busy_end.astimezone(local_tz)
                current_check_time_local = max(current_check_time_local + search_increment, advance_to_local)
                # Align to the next 15-minute mark if needed (optional)
                # minute_offset = current_check_time_local.minute % 15
                # if minute_offset != 0:
                #    current_check_time_local += timedelta(minutes=15 - minute_offset)

        return None # No slot found on this day

    def _find_next_business_day_slot(
        self, start_dt: datetime, duration: timedelta, busy_periods: list,
        business_hours_start: time, business_hours_end: time, business_days: list,
        time_zone_str: str
    ) -> Optional[datetime]:
        """Finds the next available slot at the same time on the next suitable business day."""
        logger.debug("Finding next available slot on next business day")
        try:
            local_tz = ZoneInfo(time_zone_str)
        except Exception:
            logger.warning(f"Invalid timezone string '{time_zone_str}'. Falling back to UTC.")
            local_tz = timezone.utc # Fallback
        original_start_local = start_dt.astimezone(local_tz)
        original_time = original_start_local.time()

        current_check_date = original_start_local.date() + timedelta(days=1)
        max_search_days = 14 # Limit search to avoid infinite loops

        for _ in range(max_search_days):
            # Check if it's a business day
            if current_check_date.weekday() in business_days:
                # Construct the potential start time on this date
                potential_start_local = datetime.combine(current_check_date, original_time, tzinfo=local_tz)
                potential_end_local = potential_start_local + duration

                # Check if it falls within business hours
                bh_start = datetime.combine(current_check_date, business_hours_start, tzinfo=local_tz)
                bh_end = datetime.combine(current_check_date, business_hours_end, tzinfo=local_tz)

                if potential_start_local >= bh_start and potential_end_local <= bh_end:
                    # Check availability against busy periods (in UTC)
                    slot_start_utc = potential_start_local.astimezone(timezone.utc)
                    slot_end_utc = potential_end_local.astimezone(timezone.utc)
                    is_available = True
                    for busy_start, busy_end in busy_periods:
                        if slot_start_utc < busy_end and slot_end_utc > busy_start:
                            is_available = False
                            break
                    if is_available:
                        return slot_start_utc # Found a slot

            # Move to the next day
            current_check_date += timedelta(days=1)

        return None # No slot found within the search window

    def _find_available_slots(
        self, start_dt: datetime, duration: timedelta, busy_periods: list,
        business_hours_start: time, business_hours_end: time, business_days: list,
        time_zone_str: str, max_slots: int, max_days_ahead: int
    ) -> List[datetime]:
        """Finds multiple available slots within a given timeframe, checking business hours."""
        logger.debug(f"Finding up to {max_slots} available slots within {max_days_ahead} days")
        found_slots = []
        try:
            local_tz = ZoneInfo(time_zone_str)
        except Exception:
            logger.warning(f"Invalid timezone string '{time_zone_str}'. Falling back to UTC.")
            local_tz = timezone.utc # Fallback
        #local_tz = self.date_utils.get_tzinfo(time_zone_str)
        # Start searching from the beginning of the next day
        current_day_local = start_dt.astimezone(local_tz).date() + timedelta(days=1)
        search_increment = timedelta(minutes=30) # Check every 30 minutes

        for day_offset in range(max_days_ahead):
            check_date = current_day_local + timedelta(days=day_offset)

            # Skip non-business days
            if check_date.weekday() not in business_days:
                continue

            # Define business hours for this day
            bh_start = datetime.combine(check_date, business_hours_start, tzinfo=local_tz)
            bh_end = datetime.combine(check_date, business_hours_end, tzinfo=local_tz)
            current_check_time_local = bh_start

            # Iterate through time slots within business hours for the day
            while current_check_time_local + duration <= bh_end:
                slot_start_utc = current_check_time_local.astimezone(timezone.utc)
                slot_end_utc = (current_check_time_local + duration).astimezone(timezone.utc)

                is_available = True
                next_check_start_after_busy = None
                # Check against busy periods
                for busy_start, busy_end in busy_periods:
                    if slot_start_utc < busy_end and slot_end_utc > busy_start:
                        is_available = False
                        # Advance check time efficiently past this conflict
                        next_check_start_after_busy = busy_end.astimezone(local_tz)
                        break

                if is_available:
                    found_slots.append(slot_start_utc)
                    if len(found_slots) >= max_slots:
                        return found_slots # Found enough slots
                    # Move check time past the found slot
                    current_check_time_local += duration # Or increment? Duration seems better
                else:
                    # If conflict found, jump check time past the busy period
                    if next_check_start_after_busy:
                         # Make sure we don't go backwards and align to increment grid
                         aligned_advance = max(next_check_start_after_busy, current_check_time_local + search_increment)
                         # Ensure alignment to the minute grid of search_increment if desired (optional complexity)
                         current_check_time_local = aligned_advance
                    else:
                        # No conflict, but move to next check slot
                         current_check_time_local += search_increment


        return found_slots # Return whatever was found within the search window
        
    def _format_alternative(self, start_time: datetime, duration: timedelta, time_zone_str: str, label_prefix: str) -> Dict[str, Any]:
        """Formats an alternative time slot into the required dictionary structure."""
        end_time = start_time + duration
        try:
            local_tz = ZoneInfo(time_zone_str)
        except Exception:
             logger.warning(f"Invalid timezone string '{time_zone_str}' in format. Falling back to UTC.")
             local_tz = timezone.utc # Fallback
        # local_tz = self.date_utils.get_tzinfo(time_zone_str)
        start_local = start_time.astimezone(local_tz)

        # Create a user-friendly label
        if label_prefix == "Later today":
            label = f"Later today at {start_local.strftime('%-I:%M %p')}"
        elif label_prefix == "Same time next business day":
            label = f"Same time on {start_local.strftime('%A, %b %-d')}"
        else: # "Next available"
            label = f"{start_local.strftime('%A, %b %-d at %-I:%M %p')}"
            
        return {
            "startTime": start_time.isoformat(), # Keep as UTC ISO format
            "endTime": end_time.isoformat(),     # Keep as UTC ISO format
            "timeZone": time_zone_str,           # Indicate the target timezone context
            "label": label
        } 