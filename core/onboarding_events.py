"""Signals shared by the setup wizard and the host application."""

from PySide6.QtCore import QObject, Signal


class OnboardingEvents(QObject):
    occurred = Signal(str)


def onboarding_events(player):
    events = getattr(player, "_onboarding_events", None)
    if events is None:
        events = OnboardingEvents(player)
        player._onboarding_events = events
    return events
