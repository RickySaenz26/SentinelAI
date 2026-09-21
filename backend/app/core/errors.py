from dataclasses import dataclass


@dataclass(slots=True)
class ApplicationError(Exception):
    code: str
    message: str
    status_code: int
    details: list[dict[str, object]] | None = None
    headers: dict[str, str] | None = None
