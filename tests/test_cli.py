import pytest

from trader.cli import build_parser


def test_cli_requires_event_and_mode() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
