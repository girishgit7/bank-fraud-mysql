class BusinessError(Exception):
    """A request was refused for a reason the user can act on. The message is safe to show."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


class TransferError(BusinessError):
    """A transfer or deposit was refused (e.g. insufficient funds, inactive account)."""


class RegistrationError(BusinessError):
    """Sign-up or account opening was refused (e.g. email already used, account limit reached)."""
