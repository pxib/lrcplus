"""An in-memory recording of edits used to choose factory preferences."""

from copy import deepcopy

from core.settings_transfer import get_factory_candidate


class SettingsChangeRecorder:
    HISTORY_LIMIT = 500

    def __init__(self):
        self.clear()

    def clear(self):
        self.recording = False
        self.baseline = None
        self.current = None
        self.history = []
        self.omitted_events = 0

    def start(self, payload):
        self.clear()
        self.baseline = get_factory_candidate(payload)
        self.current = deepcopy(self.baseline)
        self.recording = True

    def capture(self, payload):
        if not self.recording:
            return
        candidate = get_factory_candidate(payload)
        for section, values in candidate.items():
            for key, value in values.items():
                before = self.current[section][key]
                if before != value:
                    self.history.append({
                        "setting": f"{section}.{key}",
                        "before": deepcopy(before), "after": deepcopy(value),
                    })
        if len(self.history) > self.HISTORY_LIMIT:
            self.omitted_events += len(self.history) - self.HISTORY_LIMIT
            self.history = self.history[-self.HISTORY_LIMIT:]
        self.current = candidate

    def stop(self, payload):
        self.capture(payload)
        self.recording = False

    def changes(self):
        """Return final values that differ from the start, excluding reverted edits."""
        if self.baseline is None:
            return {}
        result = {}
        for section, values in self.current.items():
            changed = {key: deepcopy(value) for key, value in values.items()
                       if value != self.baseline[section][key]}
            if changed:
                result[section] = changed
        return result
