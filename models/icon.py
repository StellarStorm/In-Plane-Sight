from dataclasses import dataclass


@dataclass(frozen=True)
class LedIcon:
    code: str
    label: str
    palette: dict[str, str]
    rows: tuple[str, ...]

    @property
    def width(self) -> int:
        return len(self.rows[0])

    @property
    def height(self) -> int:
        return len(self.rows)
