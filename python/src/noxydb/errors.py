class NoxyDBError(Exception):
    pass


class NoxyDBValidationError(NoxyDBError, ValueError):
    pass


class NoxyDBConnectionError(NoxyDBError, ConnectionError):
    pass


class NoxyDBServerError(NoxyDBError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
