from app.chat_cli import _render_table, main


def test_chat_cli_requires_explicit_postgres_backend() -> None:
    assert main([]) == 2


def test_chat_table_renderer_handles_structured_values() -> None:
    rendered = _render_table([{"department": "Engineering", "employees": 4, "active": True}])

    assert "department" in rendered
    assert '"Engineering"' in rendered
    assert "true" in rendered
