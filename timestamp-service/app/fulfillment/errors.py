class RetryableFulfillmentError(RuntimeError):
    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


class ManualReviewError(RuntimeError):
    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


class ConfirmationPending(RuntimeError):
    """A valid proof remains normally pending and must be polled without retry exhaustion."""
