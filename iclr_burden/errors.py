class BurdenError(ValueError):
    """An input, data, or calculation error."""


class MissingCalibrationError(BurdenError):
    pass


class ScoreOverflowError(BurdenError):
    pass
