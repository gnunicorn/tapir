import datetime
from django.shortcuts import render
from django.conf import settings
from calendar import MONDAY
from collections import OrderedDict
from django.views.generic import TemplateView
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Exists, OuterRef
from tapir.shifts.models import (
    Shift,
    ShiftAttendance,
    WEEKDAY_CHOICES,
    ShiftTemplateGroup,
    ShiftTemplate,
    ShiftSlot,
)
from tapir.shifts.templatetags.shifts import shift_name_as_class
from tapir.shifts.utils import sort_slots_by_name
from tapir.shifts.utils import ColorHTMLCalendar, get_week_group
from tapir.utils.shortcuts import get_monday, set_header_for_file_download
from tapir.shifts.models import ShiftAccountEntry


# FIXME: should not be in views.py
def get_shift_slot_names():
    shift_slot_names = (
        ShiftSlot.objects.filter(shift__start_time__gt=timezone.now())
        .values_list("name", flat=True)
        .distinct()
    )
    shift_slot_names = [
        (shift_name_as_class(name), _(name)) for name in shift_slot_names if name != ""
    ]
    shift_slot_names.append(("", _("General")))
    return shift_slot_names


class UserDashboardView(LoginRequiredMixin,TemplateView):
    template_name = "shifts/user_dashboard.html"
    DATE_FORMAT = "%Y-%m-%d"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        user = self.request.user


        date_from = timezone.now().date()
        date_to = date_from + datetime.timedelta(days=60)
        context["date_from"] = date_from.strftime(self.DATE_FORMAT)
        context["date_to"] = date_to.strftime(self.DATE_FORMAT)

        context["nb_days_for_self_unregister"] = int(
            settings.NB_HOURS_FOR_SELF_UNREGISTER / 24
        )
        # Because the shift views show a lot of shifts,
        # we preload all related objects to avoid doing many database requests.
        # Filter for upcoming shifts the user can attend but isn't already attending

        now = timezone.now()

        # A slot is "blocked" iff it has a PENDING or DONE attendance. CANCELLED /
        # MISSED rows leave the slot empty and joinable; LOOKING_FOR_STAND_IN means
        # someone is looking to be replaced — also joinable.
        slot_blocked_subquery = ShiftAttendance.objects.filter(
            slot=OuterRef("pk"),
            state__in=[
                ShiftAttendance.State.PENDING,
                ShiftAttendance.State.DONE,
            ],
        )

        # `deleted=False`: deleted slots are hidden everywhere else (the shift
        # detail view uses `slots.filter(deleted=False)`), so they must not make a
        # shift look joinable here — otherwise a fully-filled shift that still has
        # a leftover deleted slot is wrongly shown as urgent/available.
        joinable_slot_subquery = ShiftSlot.objects.annotate(
            blocked=Exists(slot_blocked_subquery),
        ).filter(blocked=False, deleted=False, shift=OuterRef("pk"))

        # The user is already attending this shift if any of its slots has a valid
        # attendance owned by them. Done as Exists so the user/state pair stays on
        # the same row — bare `.exclude(user=…, state__in=…)` would over-exclude
        # because Django evaluates each lookup against potentially different rows.
        user_attending_subquery = ShiftAttendance.objects.filter(
            slot__shift=OuterRef("pk"),
            user=user,
            state__in=ShiftAttendance.VALID_STATES,
        )

        shifts = (
            Shift.objects.prefetch_related("slots")
            .prefetch_related("slots__attendances")
            .prefetch_related("slots__attendances__user")
            .prefetch_related("slots__slot_template")
            .prefetch_related("slots__slot_template__attendance_template")
            .prefetch_related("slots__slot_template__attendance_template__user")
            .prefetch_related("shift_template")
            .prefetch_related("shift_template__group")
            .prefetch_related("slots__required_capabilities")
            .annotate(
                has_joinable_slot=Exists(joinable_slot_subquery),
                user_attending=Exists(user_attending_subquery),
            )
            .filter(
                start_time__gt=now,
                start_time__lt=date_to + datetime.timedelta(days=1),
                deleted=False,
                cancelled=False,
                has_joinable_slot=True,
                user_attending=False,
            )
            .order_by("start_time")[:200]
        )

        # Final per-slot pass: capabilities are checked here because the SQL
        # version (`required_capabilities__in=…`) is wrong for multi-cap slots —
        # it matches when the user has any one of the required caps, not all.
        user_capabilities = set(user.shift_user_data.capabilities.all())

        attendable_shifts = []
        for shift in shifts:
            attendable_slots = []
            seen_slot_types = set()
            for slot in shift.slots.all():
                if slot.deleted:
                    # Mirror the SQL filter above and the shift detail view: a
                    # deleted slot is not a real, joinable slot.
                    continue
                valid = slot.get_valid_attendance()
                if (
                    valid is not None
                    and valid.state != ShiftAttendance.State.LOOKING_FOR_STAND_IN
                ):
                    continue
                if not set(slot.required_capabilities.all()).issubset(user_capabilities):
                    continue
                slot_type = slot.name if slot.name else shift.name
                if slot_type in seen_slot_types:
                    continue
                attendable_slots.append(slot)
                seen_slot_types.add(slot_type)

            if attendable_slots:
                shift.attendable_slots = attendable_slots
                attendable_shifts.append(shift)

        shifts = attendable_shifts

        # Urgent: any joinable shift starting within the next 7 days.
        urgent_cutoff = now + datetime.timedelta(days=7)

        # Use list comprehension for better performance
        urgent_shifts = [
            shift for shift in shifts
            if shift.start_time <= urgent_cutoff
        ]

        regular_shifts = [
            shift for shift in shifts
            if shift.start_time > urgent_cutoff # and
            ][:20]

        # Calculate total available slots for each category
        total_available_slots = sum(len(shift.attendable_slots) for shift in regular_shifts)
        total_urgent_slots = sum(len(shift.attendable_slots) for shift in urgent_shifts)

        context["shifts"] = regular_shifts
        context["urgent_shifts"] = urgent_shifts
        context["total_available_slots"] = total_available_slots
        context["total_urgent_slots"] = total_urgent_slots
        context["shift_slot_names"] = get_shift_slot_names()

        context["shift_account_entries"] = [
            {
                "entry": entry,
                "balance_at_date": user.shift_user_data.get_account_balance(
                    at_date=entry.date
                ),
            }
            for entry in ShiftAccountEntry.objects.filter(user=user).order_by("-date")
        ][:10]

        return context
