"""분석값을 바꾸지 않고 100만 초과 표시값만 백만 단위로 바꾼다."""

from decimal import Decimal
import re


def display_number(value: float, position: int = 0) -> str:
    """그림용 표시. 정확히 100만인 값은 원래 단위를 유지한다."""
    if abs(value) > 1_000_000:
        return f"{value / 1_000_000:,.2f}백만"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


PROTECTED = re.compile(
    r"```[\s\S]*?```|`[^`\n]+`|data:image/[^\s)\"]+|https?://[^\s)]+"
)
NUMBER = re.compile(
    r"(?<![A-Za-z0-9_/.,])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)"
    r"(?P<scale>억|만)?(?!\d|[.,]\d|[a-zA-Z_])"
)


def convert_text(text: str, english: bool = False) -> str:
    """문서의 표시 숫자만 변환하고 코드·주소·임베딩·확률식은 보존한다."""
    suffix = " million" if english else "백만"

    def convert(match: re.Match) -> str:
        original = match.group(0)
        number = match["number"]
        scale = match["scale"]
        # 이미 단위가 있는 값, 지수 표기, 비율의 분자·분모를 재변환하지 않는다.
        after = match.string[match.end() :]
        if after.startswith(("백만", " million", "(백만/million)", "/", "e-", "e+")):
            return original
        value = Decimal(number.replace(",", ""))
        if scale:
            value *= Decimal(100_000_000 if scale == "억" else 10_000)
        elif "," not in number and not after.startswith(("원", "게임", "장")):
            # 쉼표 없는 날짜·식별자 등의 큰 정수를 보호한다.
            return original
        if abs(value) > 1_000_000:
            scaled = value / 1_000_000
            rendered = (
                format(scaled, ",f").rstrip("0").rstrip(".")
                if "." in format(scaled, "f")
                else format(scaled, ",f")
            )
            return rendered + suffix
        if scale:
            return (
                format(value, ",f").rstrip("0").rstrip(".")
                if value % 1
                else format(value, ",.0f")
            )
        return original

    pieces = []
    previous = 0
    for match in PROTECTED.finditer(text):
        pieces.append(NUMBER.sub(convert, text[previous : match.start()]))
        pieces.append(match.group(0))
        previous = match.end()
    pieces.append(NUMBER.sub(convert, text[previous:]))
    return "".join(pieces).replace("백만다", "백만이다")
