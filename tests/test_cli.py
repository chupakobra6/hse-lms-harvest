from hse_lms_harvest.cli import build_parser


def test_harvest_opens_action_pages_by_default() -> None:
    parser = build_parser()

    args = parser.parse_args(["harvest", "--url", "about:blank"])

    assert args.visit_action_pages is True


def test_harvest_can_skip_action_pages_explicitly() -> None:
    parser = build_parser()

    args = parser.parse_args(["harvest", "--url", "about:blank", "--skip-action-pages"])

    assert args.visit_action_pages is False


def test_harvest_debug_defaults_keep_error_bundles_compact() -> None:
    parser = build_parser()

    args = parser.parse_args(["harvest", "--url", "about:blank"])

    assert args.debug_dump_mode == "on-error"
    assert args.debug_text_limit == 6000
