"""Deterministic evidence anchors from sanitized build logs and pipeline files."""

import re
from dataclasses import dataclass

MAX_ANCHORS = 60
_LIBRARY = r"(?:java|javax|jdk|sun|org\.junit|org\.apache|org\.opentest4j)\."
_LIBRARY_FRAME = re.compile(rf"\bat\s+(?:java\.base/)?{_LIBRARY}")
_SOURCE_REF = re.compile(
    r"((?:[A-Za-z]:)?(?:[/\\]?[\w.-]+)*[/\\][\w.-]+"
    r"\.(?:java|kt|scala|groovy|py|go|js|ts|cs|rb)"
    r"|[\w.-]+\.(?:java|kt|scala|groovy|py|go|js|ts|cs|rb))"
    r":(?:\[(\d+)(?:,\d+)?\]|(\d+))"
)
# The lookbehind stops a match from starting mid-name (e.g. at "lang." in "java.lang.X").
_CLASS = re.compile(rf"(?<![\w.$])((?:(?!{_LIBRARY})[a-z_]\w*\.)+[A-Z][\w]*)")
_MAVEN_GOAL = re.compile(r"\b(maven-[\w-]+-plugin):[\d.]+:([\w-]+)")
_GRADLE_TASK = re.compile(r"> Task :([\w:-]+)")
_SYSTEM_PROPERTY = re.compile(r"-D([a-z][\w-]*(?:\.[\w-]+)+)=")
_PROPERTY_VALUE = re.compile(r"(?i)\bproperty\w*:\s*['\"]?([a-z][\w-]*(?:\.[\w-]+)+)")
_PARAMETER = re.compile(r"\bparameters\.([A-Za-z_]\w*)|-\s*name:\s*([A-Za-z_]\w*)")
_TEST_SUFFIX = re.compile(r"(?:Tests?|IT|Spec)$")


@dataclass(frozen=True)
class Anchor:
    kind: str
    value: str
    line: int | None = None

    def label(self) -> str:
        return f"{self.kind}:{self.value}"


def log_anchors(log_text: str) -> list[Anchor]:
    found: dict[tuple[str, str, int | None], Anchor] = {}

    def add(kind: str, value: str, line: int | None = None) -> None:
        found.setdefault((kind, value, line), Anchor(kind, value, line))

    for text in log_text.splitlines():
        if _LIBRARY_FRAME.search(text):
            continue

        for match in _SOURCE_REF.finditer(text):
            path = match.group(1).replace("\\", "/")
            line = int(match.group(2) or match.group(3))

            add(
                "source_location",
                path,
                line,
            )

        for match in _CLASS.finditer(text):
            name = match.group(1).split("$")[0]
            kind = (
                "test_name"
                if _TEST_SUFFIX.search(name.rsplit(".", 1)[1])
                else "class_name"
            )
            add(kind, name)

        for match in _MAVEN_GOAL.finditer(text):
            add(
                "build_goal",
                f"{match.group(1)}:{match.group(2)}",
            )

        for match in _GRADLE_TASK.finditer(text):
            add(
                "build_goal",
                f"gradle:{match.group(1)}",
            )

        for match in _SYSTEM_PROPERTY.finditer(text):
            add(
                "property_name",
                match.group(1),
            )

    return sorted(
        found.values(),
        key=lambda a: (a.kind, a.value, a.line or 0),
    )[:MAX_ANCHORS]


def pipeline_anchors(text: str, template_paths: list[str]) -> list[Anchor]:
    found: dict[tuple[str, str], Anchor] = {}
    for match in [*_SYSTEM_PROPERTY.finditer(text), *_PROPERTY_VALUE.finditer(text)]:
        found.setdefault(("property_name", match.group(1)), Anchor("property_name", match.group(1)))
    for match in _PARAMETER.finditer(text):
        name = match.group(1) or match.group(2)
        found.setdefault(("parameter_name", name), Anchor("parameter_name", name))
    for path in template_paths:
        found.setdefault(("template_path", path), Anchor("template_path", path))
    return sorted(found.values(), key=lambda a: (a.kind, a.value))
