"""Domain errors raised by the strategic game engine."""


class StrategicGameError(ValueError):
  """Base class for invalid game operations."""


class InvalidActionError(StrategicGameError):
  """An action violates a known game rule."""


class InvalidPhaseError(StrategicGameError):
  """An operation is not allowed in the current round phase."""


class UndefinedRuleError(StrategicGameError):
  """The supplied rules do not define how to resolve this situation."""
