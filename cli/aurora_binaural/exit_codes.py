"""Exit codes and structured application errors for Aurora Binaural CLI and services.

Follows docs/plan.md Section 9:
- 0: Success (成功)
- 2: Parameter error (参数错误)
- 3: Input problem (输入问题)
- 4: Processing failure (处理失败)
- 5: Remote error (远程错误)
- 130: Canceled (取消)
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Standard CLI exit codes defined in docs/plan.md Section 9."""

    OK = 0
    PARAM_ERROR = 2
    INPUT_ERROR = 3
    PROCESSING_ERROR = 4
    REMOTE_ERROR = 5
    CANCELED = 130


class AuroraError(Exception):
    """Base structured exception carrying an exit code, stable error code, and retryability."""

    exit_code: ExitCode = ExitCode.PROCESSING_ERROR
    error_code: str = "PROCESSING_ERROR"
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        exit_code: ExitCode | None = None,
        retryable: bool | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if error_code is not None:
            self.error_code = error_code
        if exit_code is not None:
            self.exit_code = exit_code
        if retryable is not None:
            self.retryable = retryable
        self.details: dict[str, object] = details or {}

    def to_dict(self) -> dict[str, object]:
        """Serialize error to structured dictionary for JSON output (plan Section 9 & 10)."""
        payload: dict[str, object] = {
            "ok": False,
            "error": {
                "code": self.error_code,
                "message": self.message,
                "exit_code": int(self.exit_code),
                "retryable": self.retryable,
                "details": self.details,
            },
        }
        return payload


class ParameterValidationError(AuroraError):
    """Raised when CLI options or JSON parameters fail validation (Exit code 2)."""

    exit_code = ExitCode.PARAM_ERROR
    error_code = "INVALID_PARAMETERS"
    retryable = False


class InputAudioError(AuroraError):
    """Raised when input file is missing, unreadable, unsupported, or target exists (Exit code 3)."""

    exit_code = ExitCode.INPUT_ERROR
    error_code = "INPUT_ERROR"
    retryable = False


class ProcessingError(AuroraError):
    """Raised when decoding, core rendering, encoding, or readback verification fails (Exit code 4)."""

    exit_code = ExitCode.PROCESSING_ERROR
    error_code = "PROCESSING_ERROR"
    retryable = False


class RemoteServiceError(AuroraError):
    """Raised when remote API communication fails or server is unreachable (Exit code 5)."""

    exit_code = ExitCode.REMOTE_ERROR
    error_code = "REMOTE_ERROR"
    retryable = True


class OperationCanceledError(AuroraError):
    """Raised when operation is canceled via SIGINT or cancellation token (Exit code 130)."""

    exit_code = ExitCode.CANCELED
    error_code = "CANCELED"
    retryable = True
