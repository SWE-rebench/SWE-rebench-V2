# Standalone grader for the task log parser; do not edit by hand.
# Parser: parse_log_gradle_custom
from __future__ import annotations

import json
import sys
from enum import Enum
import contextlib
import fnmatch
import re

LOG_PARSER = 'parse_log_gradle_custom'


class TestStatus(str, Enum):
    FAILED = "FAILED"
    PASSED = "PASSED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"

    def __str__(self):
        return self.value


def ansi_escape(text: str) -> str:
    """
    Remove ANSI escape sequences from text
    """
    return re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|[()][A-Z0-9])").sub(
        "", text
    )


JAVA_OPTIONS_RE = re.compile(r"\s*Picked up _JAVA_OPTIONS:.*")


GRADLE_CUSTOM_STATUS_RE = re.compile(
    r"^(.+)\s+(PASSED|FAILED)(?:$|\s|[a-z]|[^A-Za-z0-9_])"
)


GRADLE_CUSTOM_PARTIAL_STATUS_RE = re.compile(
    r"^(.+?)\s+(P|PA|PAS|PASS|PASSE|F|FA|FAI|FAIL|FAILE)$"
)


GRADLE_BUILD_SUMMARY_RE = re.compile(r"^BUILD (SUCCESSFUL|FAILED)(?:$|\s)")


def _record_gradle_custom_status(
    test_status_map: ParsedTestLog, test_name: str, status: str
) -> bool:
    if status == "PASSED":
        test_status_map[test_name] = TestStatus.PASSED
        return True
    if status == "FAILED":
        test_status_map[test_name] = TestStatus.FAILED
        return True
    return False


def _record_gradle_custom_status_line(
    test_status_map: ParsedTestLog, line: str
) -> bool:
    if not (match := GRADLE_CUSTOM_STATUS_RE.match(line)):
        return False
    test_name, status = match.groups()
    return _record_gradle_custom_status(test_status_map, test_name, status)


def _consume_gradle_custom_status_prefix(
    test_status_map: ParsedTestLog,
    pending_status_prefix: tuple[str, str] | None,
    line: str,
) -> tuple[tuple[str, str] | None, bool]:
    if pending_status_prefix is None:
        return None, False

    pending_name, status_prefix = pending_status_prefix
    consumed = _record_gradle_custom_status(
        test_status_map, pending_name, f"{status_prefix}{line}"
    )
    return None, consumed


def _consume_gradle_custom_test_name(
    test_status_map: ParsedTestLog, pending_test_name: str | None, line: str
) -> tuple[str | None, bool]:
    if not pending_test_name or line not in {"PASSED", "FAILED"}:
        return pending_test_name, False

    _record_gradle_custom_status(test_status_map, pending_test_name, line)
    return None, True


def _gradle_custom_status_prefix(
    line: str, had_java_options: bool
) -> tuple[str, str] | None:
    if not had_java_options:
        return None
    if partial_match := GRADLE_CUSTOM_PARTIAL_STATUS_RE.match(line):
        test_name, status_prefix = partial_match.groups()
        return test_name, status_prefix
    return None


def parse_log_gradle_custom(log: str) -> ParsedTestLog:
    """
    Parser for test logs generated with 'gradle test'. Assumes that the
    pre-install script to update the gradle config has run.
    """
    test_status_map: ParsedTestLog = {}
    pending_test_name: str | None = None
    pending_status_prefix: tuple[str, str] | None = None
    pending_name_prefix: str | None = None

    for raw_line in log.split("\n"):
        raw_line = raw_line.strip()
        had_java_options = "Picked up _JAVA_OPTIONS:" in raw_line
        java_options_prefix = raw_line.split("Picked up _JAVA_OPTIONS:", 1)[0]
        line = JAVA_OPTIONS_RE.sub("", raw_line).strip()
        if not line or line.startswith(">"):
            continue
        if GRADLE_BUILD_SUMMARY_RE.match(line):
            continue

        if pending_name_prefix is not None:
            _record_gradle_custom_status_line(
                test_status_map, f"{pending_name_prefix}{line}"
            )
            pending_name_prefix = None

        pending_status_prefix, consumed = _consume_gradle_custom_status_prefix(
            test_status_map, pending_status_prefix, line
        )
        if consumed:
            continue

        pending_test_name, consumed = _consume_gradle_custom_test_name(
            test_status_map, pending_test_name, line
        )
        if consumed:
            continue

        if _record_gradle_custom_status_line(test_status_map, line):
            pending_test_name = None
            continue

        if pending_status_prefix := _gradle_custom_status_prefix(
            line, had_java_options
        ):
            continue

        if had_java_options:
            if ">" in line:
                pending_test_name = line
            pending_name_prefix = java_options_prefix

    return test_status_map


def _names_failed_on_any_line(test_log: str, parse_fn: TestParser) -> set[str]:
    failed: set[str] = set()
    for line in test_log.splitlines():
        if not line.strip():
            continue
        try:
            parsed_line = parse_fn(line)
        except Exception:  # noqa: BLE001
            continue
        failed.update(
            name
            for name, status in parsed_line.items()
            if status in (TestStatus.FAILED, TestStatus.ERROR)
        )
    return failed


PLACEHOLDER_TEST_NAMES = {"---NO TEST NAME FOUND YET---", "ok"}


MAVEN_SELECTOR_PLACEHOLDER_RE = re.compile(
    r'^"(?:[A-Za-z_$][\w$]*(?:Test)?(?:,[A-Za-z_$][\w$]*(?:Test)?){2,}'
    r"|[\w.*$-]*\*Test)$"
)


BUILD_SUMMARY_PLACEHOLDER_RE = re.compile(r"^\d+ modules transformed\.$")


SERVER_START_PLACEHOLDER_RE = re.compile(r"^=== \([^)]*\) Starting server\b")


JAVA_CLASS_SELECTOR_RE = re.compile(
    r"^(?:[A-Za-z_$][\w$]*\.)*[A-Z_$][\w$]*(?:Test|Tests|IT|ITCase)$"
)


JAVA_CLASS_GLOB_SELECTOR_RE = re.compile(
    r"^(?:[A-Za-z_$][\w$]*\.)*[A-Z_$*][\w$*]*(?:Test|Tests|IT|ITCase)$"
)


TEST_NAME_NORMALIZERS = (
    (
        re.compile(
            r"^\s*\d+/\d+\s+Test\s+#\d+:\s+(\S.*?)\s+\.+\s*(?:\*{3})?"
            r"(?:Passed|Failed|Not Run|Skipped|Timeout|Exception)\b.*$"
        ),
        r"\1",
    ),
    (
        re.compile(
            r"(?:\s+\[\d+(?:\.\d+)?\s*ms\]"
            r"|\s+\(\d+(?:\.\d+)?\s*m?s\)"
            r"|\s+\(\d+(?:\.\d+)?\s*ms\s+and\s+\d+\s+assertions\)"
            r"|\s+\d+(?:\.\d+)?m?s)$"
        ),
        "",
    ),
    (re.compile(r"\$\$Lambda/0x[0-9a-fA-F]+"), "$$Lambda/0x<id>"),
    (re.compile(r"@[0-9a-fA-F]{4,}\b"), "@<id>"),
    (re.compile(r"0x[0-9a-fA-F]{7,}"), "0x<addr>"),
    (re.compile(r"&\{\[[0-9_ ]{9,}\]\}"), "&{[<addr>]}"),
    (re.compile(r"\b(127\.0\.0\.1|localhost|0\.0\.0\.0):\d+\b"), r"\1:<port>"),
    (
        re.compile(
            r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?"
            r"(?:Z(?:\[[^\]]+\])?|[+-]\d{2}:\d{2})?\b"
        ),
        "<timestamp>",
    ),
    (
        re.compile(
            r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
            r"\d{2} \d{4} \d{2}:\d{2}:\d{2} GMT[+-]\d{4} \([^)]*\)"
        ),
        "<date>",
    ),
    (
        re.compile(
            r"(?<=\[)(?:\d{10}\.\d+|\d{2}-\d{2}-\d{4}|\d{2}:\d{2}:\d{2})(?=\]|-)"
        ),
        "<param>",
    ),
    (re.compile(r"\b(Compiled in )\d+(?:\.\d+)?s\b"), r"\1<duration>s"),
    (re.compile(r"^\d+(?:\.\d+)?%\s+passed$"), "<percent> passed"),
    (re.compile(r"/tmp/[^/\s,)]+"), "/tmp/<tmp>"),
    (re.compile(r"\s+ran (?:\d+|ZERO) tests?\b.*$"), ""),
    (re.compile(r"^\[\s*#\d+\]\s+"), ""),
    (re.compile(r"^(?!\[ok\]: ).+?(?=\[ok\]: )"), ""),
    (re.compile(r"\s+\(cached\)$"), ""),
    (re.compile(r"^\d+\s+(?=\[[\w.-]+\] \u203a )"), ""),
    (re.compile(r"(?<![\w.])\d+\.\d{6,}(?:e-?\d+)?\b"), "<float>"),
)


def _strip_shell_quotes(value: str) -> str:
    value = value.strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return value.strip("'\"").strip()


def _normalize_test_name_for_compare(value: str) -> str:
    with contextlib.suppress(UnicodeError):
        value = value.encode("latin1").decode("utf-8")
    value = _strip_shell_quotes(ansi_escape(value))
    for pattern, replacement in TEST_NAME_NORMALIZERS:
        value = pattern.sub(replacement, value)
    return _strip_shell_quotes(value)


def _is_placeholder_test_name(name: str) -> bool:
    if (
        name in PLACEHOLDER_TEST_NAMES
        or BUILD_SUMMARY_PLACEHOLDER_RE.fullmatch(name)
        or SERVER_START_PLACEHOLDER_RE.search(name)
    ):
        return True
    return MAVEN_SELECTOR_PLACEHOLDER_RE.fullmatch(
        name
    ) is not None and not _is_java_class_glob_selector(name)


def _normalized_passed_names(names: set[str]) -> set[str]:
    normalized = {_normalize_test_name_for_compare(name) for name in names}
    return {name for name in normalized if name and not _is_placeholder_test_name(name)}


def _is_java_class_selector(name: str) -> bool:
    return JAVA_CLASS_SELECTOR_RE.fullmatch(name) is not None


def _is_java_class_glob_selector(name: str) -> bool:
    return "*" in name and JAVA_CLASS_GLOB_SELECTOR_RE.fullmatch(name) is not None


def _split_java_class_selectors(name: str) -> list[str] | None:
    if "," not in name or "(" in name or any(char.isspace() for char in name):
        return None
    parts = [part.strip() for part in name.split(",") if part.strip()]
    if len(parts) < 2 or not all(
        _is_java_class_selector(part) or _is_java_class_glob_selector(part)
        for part in parts
    ):
        return None
    return parts


def _java_class_aliases(class_name: str) -> set[str]:
    return {class_name, class_name.rsplit(".", 1)[-1]}


def _java_class_name_from_test_name(name: str) -> str | None:
    if _is_java_class_selector(name):
        return name
    return None


def _actual_passed_aliases(names: set[str]) -> set[str]:
    aliases: set[str] = set()
    for name in names:
        aliases.add(name)
        if class_name := _java_class_name_from_test_name(name):
            aliases.update(_java_class_aliases(class_name))
        if " " in name:
            class_name, method_name = name.split(None, 1)
            if _is_java_class_selector(class_name):
                simple_class = class_name.rsplit(".", 1)[-1]
                aliases.add(f"{simple_class} {method_name}")
    return aliases


def _java_method_selector_aliases(name: str) -> set[str]:
    if "#" not in name:
        return set()
    class_name, method_name = name.split("#", 1)
    method_name = method_name.rstrip("()")
    aliases = {f"{class_name} {method_name}", f"{class_name} {method_name}()"}
    if _is_java_class_selector(class_name):
        simple_class = class_name.rsplit(".", 1)[-1]
        aliases.update(
            {f"{simple_class} {method_name}", f"{simple_class} {method_name}()"}
        )
    return aliases


def _expected_selector_satisfied(actual_passed: set[str], expected_name: str) -> bool:
    if _is_java_class_glob_selector(expected_name):
        return any(fnmatch.fnmatchcase(name, expected_name) for name in actual_passed)

    aliases = {expected_name}
    aliases.update(_java_method_selector_aliases(expected_name))
    if _is_java_class_selector(expected_name):
        aliases.update(_java_class_aliases(expected_name))
    return not aliases.isdisjoint(actual_passed)


def _expected_name_satisfied(actual_passed: set[str], expected_name: str) -> bool:
    selector_parts = _split_java_class_selectors(expected_name) or [expected_name]
    return all(
        _expected_selector_satisfied(actual_passed, selector)
        for selector in selector_parts
    )


def normalized_passed_subset(actual: TestResults, expected: TestResults) -> bool:
    actual_passed = _actual_passed_aliases(_normalized_passed_names(actual["PASSED"]))
    expected_passed = _normalized_passed_names(expected["PASSED"])
    return bool(expected_passed) and all(
        _expected_name_satisfied(actual_passed, expected_name)
        for expected_name in expected_passed
    )


def parse_test_log(test_log):
    test_log = ansi_escape(test_log).replace("\r", "\n")
    parsed = parse_log_gradle_custom(test_log)
    for name in _names_failed_on_any_line(test_log, parse_log_gradle_custom) & parsed.keys():
        if parsed[name] == TestStatus.PASSED:
            parsed[name] = TestStatus.FAILED
    results = {"FAILED": set(), "PASSED": set(), "SKIPPED": set(), "ERROR": set()}
    for name, status in parsed.items():
        results[status.value].add(name)
    return results


def grade(config, test_log):
    fail_to_pass = config["FAIL_TO_PASS"]
    pass_to_pass = config["PASS_TO_PASS"]
    results = parse_test_log(test_log)
    expected = {"PASSED": set(fail_to_pass) | set(pass_to_pass)}
    actual_passed = _actual_passed_aliases(_normalized_passed_names(results["PASSED"]))

    def status(names):
        return {
            name: all(
                _expected_name_satisfied(actual_passed, normalized)
                for normalized in _normalized_passed_names({name})
            )
            for name in names
        }

    return {
        "log_parser": LOG_PARSER,
        "resolved": normalized_passed_subset(results, expected),
        "parsed_counts": {key: len(value) for key, value in results.items()},
        "fail_to_pass": status(fail_to_pass),
        "pass_to_pass": status(pass_to_pass),
        "test_results": {key: sorted(value) for key, value in results.items()},
    }


def main():
    config_path, log_path, report_path = sys.argv[1:4]
    with open(config_path, encoding="utf-8") as fh:
        config = json.load(fh)
    with open(log_path, encoding="utf-8", errors="replace") as fh:
        report = grade(config, fh.read())
    test_results = report.pop("test_results")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(report, indent=2) + "\n")
    if len(sys.argv) > 4:
        with open(sys.argv[4], "w", encoding="utf-8") as fh:
            fh.write(json.dumps(test_results) + "\n")
    f2p_ok = sum(report["fail_to_pass"].values())
    p2p_ok = sum(report["pass_to_pass"].values())
    print("parser=%s parsed=%s" % (LOG_PARSER, report["parsed_counts"]))
    print("F2P %d/%d P2P %d/%d" % (f2p_ok, len(report["fail_to_pass"]), p2p_ok, len(report["pass_to_pass"])))
    print("RESOLVED" if report["resolved"] else "UNRESOLVED")
    sys.exit(0 if report["resolved"] else 1)


if __name__ == "__main__":
    main()
