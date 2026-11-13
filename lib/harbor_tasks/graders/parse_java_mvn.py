# Standalone grader for the task log parser; do not edit by hand.
# Parser: parse_java_mvn
from __future__ import annotations

import json
import sys
from enum import Enum
from bisect import bisect_left
import contextlib
import fnmatch
import re

LOG_PARSER = 'parse_java_mvn'


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


def parse_log_maven(log: str) -> ParsedTestLog:
    """
    Parser for test logs generated with 'mvn test'.
    Annoyingly maven will not print the tests that have succeeded. For this log
    parser to work, each test must be run individually, and then we look for
    BUILD (SUCCESS|FAILURE) in the logs.

    Args:
        log (str): log content
    Returns:
        dict: test case to test status mapping
    """
    test_status_map: ParsedTestLog = {}
    current_test_name = "---NO TEST NAME FOUND YET---"

    test_name_pattern = r"^.*-Dtest=(\S+).*$"
    running_pattern = r"^\[INFO\]\s+Running\s+(\S+)$"
    result_pattern = r"^.*BUILD (SUCCESS|FAILURE)$"

    for line in log.split("\n"):
        test_name_match = re.match(test_name_pattern, line.strip())
        if test_name_match:
            current_test_name = test_name_match.groups()[0]
            continue

        running_match = re.match(running_pattern, line.strip())
        if running_match:
            current_test_name = running_match.groups()[0]
            continue

        result_match = re.match(result_pattern, line.strip())
        if result_match:
            status = result_match.groups()[0]
            if status == "SUCCESS":
                test_status_map[current_test_name] = TestStatus.PASSED
            elif status == "FAILURE":
                test_status_map[current_test_name] = TestStatus.FAILED

    return test_status_map


def _mvn_failure_status(line: str) -> TestStatus:
    if "Exception" in line and "AssertionError" not in line:
        return TestStatus.ERROR
    return TestStatus.FAILED


def _mvn_summary_class(line: str) -> str | None:
    if " in " in line:
        return line.rsplit(" in ", 1)[-1].strip().rstrip(".:;")
    return None


def _mvn_status_from_summary_counts(
    tests_run: int, failures: int, errors: int, skipped: int
) -> TestStatus:
    if failures == 0 and errors == 0:
        if tests_run > skipped or skipped == 0:
            return TestStatus.PASSED
        return TestStatus.SKIPPED
    return TestStatus.FAILED


def _record_mvn_gradle_test_status(
    results: ParsedTestLog, current_gradle_class: str | None, match: re.Match[str]
) -> None:
    if not current_gradle_class:
        return
    method, status_word = match.groups()
    status = TestStatus.PASSED if status_word == "PASSED" else TestStatus.FAILED
    results[f"{current_gradle_class} {method}"] = status
    if status is TestStatus.FAILED:
        results[current_gradle_class] = status
    else:
        results.setdefault(current_gradle_class, status)


def _record_mvn_failure(
    results: ParsedTestLog, line: str, match: re.Match[str]
) -> None:
    clazz = match.group("class")
    method = match.group("method")
    test_id = f"{clazz}.{method}"
    results[test_id] = _mvn_failure_status(line)
    results[clazz] = TestStatus.FAILED


def _record_mvn_summary(
    results: ParsedTestLog,
    line: str,
    match: re.Match[str],
    current_running: str | None,
) -> None:
    tests_run, failures, errors, skipped = map(int, match.groups())
    clazz = _mvn_summary_class(line)
    result_name = clazz or current_running
    if result_name:
        status = _mvn_status_from_summary_counts(tests_run, failures, errors, skipped)
        if status is TestStatus.FAILED:
            results[result_name] = status
        else:
            results.setdefault(result_name, status)


def _nested_statuses(
    names: list[str], results: ParsedTestLog, parent: str
) -> set[TestStatus]:
    prefix = f"{parent}$"
    statuses: set[TestStatus] = set()
    index = bisect_left(names, prefix)
    while index < len(names) and names[index].startswith(prefix):
        statuses.add(results[names[index]])
        index += 1
    return statuses


def _promote_nested_class_parents(results: ParsedTestLog) -> None:
    names = sorted(results)
    promoted: dict[str, TestStatus] = {}
    for parent in names:
        if " " in parent:
            continue
        statuses = _nested_statuses(names, results, parent)
        if statuses & {TestStatus.FAILED, TestStatus.ERROR}:
            promoted[parent] = TestStatus.FAILED
        elif results[parent] is TestStatus.SKIPPED and TestStatus.PASSED in statuses:
            promoted[parent] = TestStatus.PASSED
    results.update(promoted)


def parse_java_mvn(log: str) -> ParsedTestLog:
    """Parse Maven logs into {identifier: status}."""
    results: ParsedTestLog = {}

    failure_re = re.compile(
        r"^\[ERROR\]\s+(?P<class>[A-Za-z0-9_$.]+)\.(?P<method>[A-Za-z0-9_$.]+?)(?::(?P<line>\d+))?\b"
    )
    running_re = re.compile(r"^(?:\[INFO\]\s+)?Running\s+([A-Za-z0-9_$.]+)$")
    gradle_class_re = re.compile(r"^([A-Za-z0-9_$.]+(?:Test|Tests|IT|ITCase))$")
    gradle_test_status_re = re.compile(
        r"^Test\s+(.+?)\s+(PASSED|FAILED)(?:\s+\(\d+(?:\.\d+)?\s*m?s\))?$"
    )
    summary_re = re.compile(
        r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),"
        r"\s*Skipped:\s*(\d+)"
    )

    seen_running: set[str] = set()
    current_running: str | None = None
    current_gradle_class: str | None = None

    for raw in log.splitlines():
        line = raw.strip()
        if not line:
            continue
        if rm := running_re.match(line):
            running_name = rm.group(1)
            current_running = running_name
            current_gradle_class = running_name
            seen_running.add(running_name)
            continue
        if gm := gradle_class_re.match(line):
            current_gradle_class = gm.group(1)
            seen_running.add(current_gradle_class)
            continue
        if tm := gradle_test_status_re.match(line):
            _record_mvn_gradle_test_status(results, current_gradle_class, tm)
            continue
        if fm := failure_re.match(line):
            _record_mvn_failure(results, line, fm)
            continue

        if sm := summary_re.search(line):
            _record_mvn_summary(results, line, sm, current_running)
            current_running = None

    for test_name, status in parse_log_junit(log).items():
        results.setdefault(test_name, status)

    for test_name, status in parse_log_maven(log).items():
        results.setdefault(test_name, status)

    if not results and seen_running:
        for clazz in seen_running:
            results.setdefault(clazz, TestStatus.PASSED)

    _promote_nested_class_parents(results)
    return results


def _parse_junit_testcases_from_text(log: str) -> ParsedTestLog:
    results: ParsedTestLog = {}
    open_tag_re = re.compile(r"<testcase\b([^>]*)>", re.DOTALL)
    attr_re = re.compile(r'(\w+)="([^"]*)"')

    pos = 0
    while True:
        match = open_tag_re.search(log, pos)
        if match is None:
            break

        raw_attrs = match.group(1) or ""
        attrs = dict(attr_re.findall(raw_attrs))
        name = attrs.get("name")
        classname = attrs.get("classname", "")

        if not name:
            pos = match.end()
            continue

        is_self_closing = raw_attrs.strip().endswith("/")
        content = ""
        if is_self_closing:
            pos = match.end()
        else:
            close_pos = log.find("</testcase>", match.end())
            if close_pos != -1:
                content = log[match.end() : close_pos]
                pos = close_pos + len("</testcase>")
            else:
                content = log[match.end() : match.end() + 4096]
                pos = match.end()

        full_name = f"{classname} {name}".strip() if classname else name

        if "<failure" in content:
            status = TestStatus.FAILED
        elif "<error" in content:
            status = TestStatus.ERROR
        elif "<skipped" in content:
            status = TestStatus.SKIPPED
        else:
            status = TestStatus.PASSED

        results[full_name] = status

    return results


def parse_log_junit(log: str) -> ParsedTestLog:
    """Parse Scala sbt test output (JUnit XML format).

    Returns {full_test_name: status}.

    Rules:
      * Parse XML testsuite elements from the log
      * Extract testcase elements with their name and classname
      * Determine status: PASSED (no failure/error/skipped), FAILED, ERROR,
        or SKIPPED
    """
    return _parse_junit_testcases_from_text(log)


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
    parsed = parse_java_mvn(test_log)
    for name in _names_failed_on_any_line(test_log, parse_java_mvn) & parsed.keys():
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
