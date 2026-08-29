"""The `wp-todo sources` commands.

A verdict is only worth recording if recording it is a five-second action taken
right after the source was checked, while the reason is still in the editor's
head. So these tests are mostly about the command refusing to make that easy in
the wrong ways - no reason, a URL instead of a host, a typo'd verdict.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wp_todo.cli import app
from wp_todo.sources import load_ledger

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"


def scope_with_sources(tmp_path: Path) -> tuple[Path, Path]:
    """A scope file pointing at a throwaway verdict file."""
    sources = tmp_path / "sources.toml"
    sources.write_text("# handgeschriebener Kommentar\n", encoding="utf-8")
    scope = tmp_path / "scope.toml"
    scope.write_text(
        FIXTURES.joinpath("scope.toml").read_text(encoding="utf-8")
        + f'\n[research]\nsources = "{sources}"\n',
        encoding="utf-8",
    )
    return scope, sources


def test_a_verdict_is_recorded_and_readable_back(tmp_path: Path) -> None:
    scope, sources = scope_with_sources(tmp_path)

    result = runner.invoke(
        app, ["sources", "block", "beispiel.example", "--reason", "Datendump von 2015", "-c", str(scope)]
    )

    assert result.exit_code == 0, result.output
    ledger = load_ledger(sources)
    assert [v.domain for v in ledger.verdicts] == ["beispiel.example"]
    assert ledger.verdicts[0].reason == "Datendump von 2015"


def test_the_command_says_what_will_happen_because_of_it(tmp_path: Path) -> None:
    """Three verdicts with different consequences is two too many to remember."""
    scope, _ = scope_with_sources(tmp_path)

    blocked = runner.invoke(app, ["sources", "block", "a.example", "--reason", "x", "-c", str(scope)])
    trusted = runner.invoke(app, ["sources", "trust", "b.example", "--reason", "y", "-c", str(scope)])

    assert "nicht mehr abgerufen" in blocked.output
    assert "Sortieren bevorzugt" in trusted.output
    assert "Spiegel-Erkennung bleiben" in trusted.output, "trust must not read as a bypass"


def test_a_reason_is_required(tmp_path: Path) -> None:
    scope, sources = scope_with_sources(tmp_path)

    result = runner.invoke(app, ["sources", "block", "beispiel.example", "-c", str(scope)])

    assert result.exit_code != 0
    assert load_ledger(sources).verdicts == ()


def test_an_empty_reason_is_refused_with_the_reasoning(tmp_path: Path) -> None:
    """The NPOV mitigation only works if the message explains itself."""
    scope, sources = scope_with_sources(tmp_path)

    result = runner.invoke(app, ["sources", "block", "beispiel.example", "--reason", "   ", "-c", str(scope)])

    assert result.exit_code == 2
    assert "disagree with it" in result.output
    assert load_ledger(sources).verdicts == ()


def test_a_url_is_refused_with_a_hint(tmp_path: Path) -> None:
    scope, sources = scope_with_sources(tmp_path)

    result = runner.invoke(
        app, ["sources", "block", "https://beispiel.example/x", "--reason", "y", "-c", str(scope)]
    )

    assert result.exit_code == 2
    assert "not a domain" in result.output
    assert load_ledger(sources).verdicts == ()


def test_recording_preserves_hand_written_content(tmp_path: Path) -> None:
    """Appending rather than rewriting is the whole reason the file stays
    hand-editable."""
    scope, sources = scope_with_sources(tmp_path)

    runner.invoke(app, ["sources", "note", "eins.example", "--reason", "a", "-c", str(scope)])
    runner.invoke(app, ["sources", "note", "zwei.example", "--reason", "b", "-c", str(scope)])

    text = sources.read_text(encoding="utf-8")
    assert "# handgeschriebener Kommentar" in text
    assert {v.domain for v in load_ledger(sources).verdicts} == {"eins.example", "zwei.example"}


def test_list_is_empty_before_anything_is_recorded(tmp_path: Path) -> None:
    scope, _ = scope_with_sources(tmp_path)

    result = runner.invoke(app, ["sources", "list", "-c", str(scope)])

    assert result.exit_code == 0
    assert "nothing recorded" in result.output


def test_list_filters_by_verdict(tmp_path: Path) -> None:
    scope, _ = scope_with_sources(tmp_path)
    runner.invoke(app, ["sources", "block", "a.example", "--reason", "x", "-c", str(scope)])
    runner.invoke(app, ["sources", "trust", "b.example", "--reason", "y", "-c", str(scope)])

    result = runner.invoke(app, ["sources", "list", "--verdict", "block", "-c", str(scope)])

    assert "a.example" in result.output
    assert "b.example" not in result.output


def test_a_corrupt_verdict_file_fails_loudly(tmp_path: Path) -> None:
    """A silently ignored verdict file would mean silently un-blocking things."""
    scope, sources = scope_with_sources(tmp_path)
    sources.write_text('[[source]]\ndomain = "x.example"\nverdict = "block"\n', encoding="utf-8")

    result = runner.invoke(app, ["sources", "list", "-c", str(scope)])

    assert result.exit_code == 2
    assert "reason" in result.output
