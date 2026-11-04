import os

from leadbot.env import load_dotenv


def _write(tmp_path, text):
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_quoted_unquoted_and_comments(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    path = _write(tmp_path, '# comment\nT_A="dq value"\nT_B=\'sq\'\nT_C=plain\n\n'
                            'T_D="mismatch\'\nT_E = " spaced "\n')
    load_dotenv(path)
    assert os.environ["T_A"] == "dq value"
    assert os.environ["T_B"] == "sq"
    assert os.environ["T_C"] == "plain"
    assert os.environ["T_D"] == "\"mismatch'"
    assert os.environ["T_E"] == " spaced "


def test_real_environment_variable_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ, T_REAL="from-env"))
    load_dotenv(_write(tmp_path, 'T_REAL="from-file"\n'))
    assert os.environ["T_REAL"] == "from-env"


def test_missing_file_is_ignored(tmp_path):
    load_dotenv(str(tmp_path / "nope.env"))
