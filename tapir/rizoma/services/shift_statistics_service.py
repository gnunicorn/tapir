from collections import defaultdict

from django.db.models import Exists, OuterRef

from tapir.shifts.config import DEFAULT_SLOT_ORDER
from tapir.shifts.models import ShiftAttendanceTemplate, ShiftSlotTemplate


class RizomaShiftStatisticsService:
    """Statistics about the regular (ABCD) shift schedule, reproducing the top block
    of the GT-Membros monthly report ("turnos regulares vazios")."""

    @classmethod
    def get_empty_abcd_shifts_by_type(cls) -> dict:
        """Count empty regular (ABCD) shifts per slot type.

        A "regular shift of type X" is the group of slot templates with the same name
        within a single ABCD shift template. A slot is considered taken when its
        template has a permanent attendance (``ShiftAttendanceTemplate``). For each
        group we classify:
          - ``completely_empty``: no slot is taken,
          - ``partial``: some but not all slots are taken (e.g. a double shift with one
            person),
          - ``empty`` = ``completely_empty`` + ``partial`` (at least one free slot).
        Fully-taken groups are not counted.

        Returns a dict with ``rows`` (ordered per type) and ``totals``.
        """
        slot_templates = (
            ShiftSlotTemplate.objects.filter(
                deleted=False,
                shift_template__group__isnull=False,
            )
            .annotate(
                has_attendant=Exists(
                    ShiftAttendanceTemplate.objects.filter(
                        slot_template=OuterRef("pk")
                    )
                )
            )
            .values_list("shift_template_id", "name", "has_attendant")
        )

        # Group by (shift_template, slot name): how many slots, how many taken.
        groups = defaultdict(lambda: {"total": 0, "filled": 0})
        for shift_template_id, name, has_attendant in slot_templates:
            group = groups[(shift_template_id, name)]
            group["total"] += 1
            if has_attendant:
                group["filled"] += 1

        per_type = defaultdict(
            lambda: {"empty": 0, "partial": 0, "completely_empty": 0}
        )
        for (_shift_template_id, name), counts in groups.items():
            if counts["filled"] == 0:
                per_type[name]["completely_empty"] += 1
                per_type[name]["empty"] += 1
            elif counts["filled"] < counts["total"]:
                per_type[name]["partial"] += 1
                per_type[name]["empty"] += 1

        rows = [
            {"type": name, **per_type[name]} for name in cls._ordered_names(per_type)
        ]
        totals = {
            key: sum(row[key] for row in rows)
            for key in ("empty", "partial", "completely_empty")
        }
        return {"rows": rows, "totals": totals}

    @staticmethod
    def _ordered_names(per_type: dict) -> list:
        """Names ordered by DEFAULT_SLOT_ORDER, with any extras appended alphabetically."""
        order = {name: index for index, name in enumerate(DEFAULT_SLOT_ORDER)}
        return sorted(
            per_type.keys(),
            key=lambda name: (order.get(name.casefold(), len(order)), name.casefold()),
        )
