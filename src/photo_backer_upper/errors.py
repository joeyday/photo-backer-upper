"""Errors the commands raise on purpose; `main` turns them into exit codes."""


class ConfigError(Exception):
    pass


class LockBusy(Exception):
    def __init__(self, message: str, held_seconds: float = 0):
        super().__init__(message)
        self.held_seconds = held_seconds


class SecondaryUnavailable(ConfigError):
    """No secondary drive is configured or attached; `run` skips the mirror for this."""
