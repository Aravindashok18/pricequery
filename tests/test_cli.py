import pytest

from pricequery import cli, config


@pytest.fixture(autouse=True)
def demo_db(demo_url, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", demo_url)


def test_sql_flag_prints_a_table(capsys):
    cli.main(["--sql", "SELECT name FROM categories ORDER BY name LIMIT 2"])
    out = capsys.readouterr().out
    assert "Cooking" in out and "Fiction" in out and "(2 rows)" in out


def test_sql_flag_refuses_writes_with_a_reason():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--sql", "DELETE FROM products"])
    assert "refused" in str(exc.value) and "DELETE" in str(exc.value)


def test_question_without_an_api_key_explains_what_to_do(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    with pytest.raises(SystemExit) as exc:
        cli.main(["How many products are there?"])
    assert "OPENAI_API_KEY" in str(exc.value)
