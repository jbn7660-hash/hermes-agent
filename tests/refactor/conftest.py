"""Refactor-test conftest.

Registers the --update-fixtures CLI flag used by snapshot tests in
tests/refactor/ to regenerate their baseline JSON files.
"""


def pytest_addoption(parser):
    parser.addoption(
        "--update-fixtures",
        action="store_true",
        default=False,
        help="Capture fresh refactor-test baseline JSON instead of asserting.",
    )
