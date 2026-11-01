"""Safe errors never expose source records or database connection details."""


class CourseError(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status, self.code = status, code
        super().__init__(code)
