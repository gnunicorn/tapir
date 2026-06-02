from django.test import TestCase

from tapir.accounts.tests.factories.factories import TapirUserFactory
from tapir.shifts.models import (
    ShiftAttendanceTemplate,
    ShiftTemplateGroup,
)
from tapir.shifts.tests.factories import (
    ShiftSlotTemplateFactory,
    ShiftTemplateFactory,
)
from tapir.rizoma.services.shift_statistics_service import RizomaShiftStatisticsService


class TestRizomaShiftStatisticsService(TestCase):
    @classmethod
    def _make_shift_template(cls, group, slots):
        """Create an ABCD shift template with the given slots.

        ``slots`` is a list of ``(name, filled)`` tuples; ``filled`` controls whether a
        ``ShiftAttendanceTemplate`` (permanent member) is attached to the slot.
        """
        shift_template = ShiftTemplateFactory.create(group=group, nb_slots=0)
        for name, filled in slots:
            slot_template = ShiftSlotTemplateFactory.create(
                shift_template=shift_template, name=name
            )
            if filled:
                ShiftAttendanceTemplate.objects.create(
                    user=TapirUserFactory.create(), slot_template=slot_template
                )
        return shift_template

    def _rows_by_type(self, result):
        return {row["type"]: row for row in result["rows"]}

    def test_classifiesCompletelyEmptyPartialAndFull(self):
        group = ShiftTemplateGroup.objects.create(name="A")
        # Caixa: a double shift with nobody -> completely empty.
        self._make_shift_template(group, [("Caixa", False), ("Caixa", False)])
        # Mercearia: a double shift with one person -> partial.
        self._make_shift_template(group, [("Mercearia", True), ("Mercearia", False)])
        # Rizobar: a single slot, taken -> full, not counted.
        self._make_shift_template(group, [("Rizobar", True)])

        result = RizomaShiftStatisticsService.get_empty_abcd_shifts_by_type()
        rows = self._rows_by_type(result)

        self.assertEqual(
            {"type": "Caixa", "empty": 1, "partial": 0, "completely_empty": 1},
            rows["Caixa"],
        )
        self.assertEqual(
            {"type": "Mercearia", "empty": 1, "partial": 1, "completely_empty": 0},
            rows["Mercearia"],
        )
        # A fully-taken group contributes nothing, so Rizobar should not appear.
        self.assertNotIn("Rizobar", rows)
        self.assertEqual(
            {"empty": 2, "partial": 1, "completely_empty": 1}, result["totals"]
        )

    def test_excludesNonAbcdAndDeletedSlots(self):
        group = ShiftTemplateGroup.objects.create(name="A")
        self._make_shift_template(group, [("Caixa", False)])

        # Not part of the ABCD schedule (no group) -> excluded.
        self._make_shift_template(None, [("Caixa", False)])

        # Deleted slot template -> excluded.
        deleted_template = ShiftTemplateFactory.create(group=group, nb_slots=0)
        ShiftSlotTemplateFactory.create(
            shift_template=deleted_template, name="Caixa", deleted=True
        )

        result = RizomaShiftStatisticsService.get_empty_abcd_shifts_by_type()
        rows = self._rows_by_type(result)

        self.assertEqual(1, rows["Caixa"]["completely_empty"])
        self.assertEqual(1, result["totals"]["completely_empty"])

    def test_ordersRowsByDefaultSlotOrder(self):
        group = ShiftTemplateGroup.objects.create(name="A")
        for name in ["Rizochef", "Caixa", "Zebra", "Mercearia"]:
            self._make_shift_template(group, [(name, False)])

        result = RizomaShiftStatisticsService.get_empty_abcd_shifts_by_type()
        ordered_types = [row["type"] for row in result["rows"]]

        # Known types follow DEFAULT_SLOT_ORDER; unknown ("Zebra") is appended last.
        self.assertEqual(["Caixa", "Mercearia", "Rizochef", "Zebra"], ordered_types)
