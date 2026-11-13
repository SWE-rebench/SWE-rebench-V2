# Standalone grader for the task log parser; do not edit by hand.
# Parser: parse_log_ocaml_v2
from __future__ import annotations

import json
import sys
from enum import Enum
import contextlib
import fnmatch
import re

LOG_PARSER = 'parse_log_ocaml_v2'


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


OCAML_STATUS_PRECEDENCE = {
    TestStatus.PASSED: 0,
    TestStatus.SKIPPED: 1,
    TestStatus.FAILED: 2,
    TestStatus.ERROR: 3,
}


class _OcamlDuneLogParser:
    running_re = re.compile(r"^Running\[(?P<id>\d+)\]:\s+\((?P<command>.+)\)$")
    output_re = re.compile(r"^Output\[(?P<id>\d+)]:")
    fail_tokens = ("FAIL", "ERROR", "EXCEPTION", "CRASH", "FATAL")
    skip_tokens = ("SKIP", "SKIPPED")

    def __init__(self) -> None:
        self.results: ParsedTestLog = {}
        self.run_lookup: dict[int, str] = {}
        self.active_test: str | None = None
        self.seen_output_tests: set[str] = set()

    @staticmethod
    def _extract_test_name(command_segment: str) -> str:
        command = command_segment.split("&&")[-1].strip()
        if command.startswith("exec "):
            command = command[5:].strip()
        if command.startswith("./"):
            command = command[2:]
        parts = command.split()
        binary = parts[0] if parts else ""
        return binary.rsplit("/", 1)[-1] if binary else ""

    def _update_status(self, name: str, status: TestStatus) -> None:
        if not name:
            return
        current = self.results.get(name)
        if current is None:
            self.results[name] = status
            return
        if OCAML_STATUS_PRECEDENCE.get(status, -1) >= OCAML_STATUS_PRECEDENCE.get(
            current, -1
        ):
            self.results[name] = status

    def handle_line(self, raw_line: str) -> None:
        line = raw_line.strip()
        if not line:
            return
        if match := self.running_re.match(line):
            run_id = int(match.group("id"))
            test_name = self._extract_test_name(match.group("command"))
            self.run_lookup[run_id] = test_name or f"run_{run_id}"
            self.active_test = None
            return
        if match := self.output_re.match(line):
            self.active_test = self.run_lookup.get(int(match.group("id")))
            return
        if not self.active_test:
            return
        self.seen_output_tests.add(self.active_test)
        upper = line.upper()
        if any(token in upper for token in self.fail_tokens):
            self._update_status(self.active_test, TestStatus.FAILED)
        elif any(token in upper for token in self.skip_tokens):
            self._update_status(self.active_test, TestStatus.SKIPPED)
        elif upper == "OK":
            self._update_status(self.active_test, TestStatus.PASSED)

    def finalize(self) -> ParsedTestLog:
        for test_name in self.seen_output_tests:
            self.results.setdefault(test_name, TestStatus.PASSED)
        return self.results


def parse_log_ocaml_v2(log: str) -> ParsedTestLog:
    """Parse dune test logs and return {test_binary: status}."""
    parser = _OcamlDuneLogParser()
    for raw_line in log.splitlines():
        parser.handle_line(raw_line)
    return parser.finalize()


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
    parsed = parse_log_ocaml_v2(test_log)
    for name in _names_failed_on_any_line(test_log, parse_log_ocaml_v2) & parsed.keys():
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
