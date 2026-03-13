from __future__ import annotations

from pathlib import Path

from twitter_cli.config import DEFAULT_CONFIG, load_config


def test_load_config_supports_block_list_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "\n".join(
            [
                "fetch:",
                "  count: 25",
                "filter:",
                "  mode: score",
                "  lang:",
                "    - en",
                "    - zh",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(str(config_file))
    assert config["fetch"]["count"] == 25
    assert config["filter"]["mode"] == "score"
    assert config["filter"]["lang"] == ["en", "zh"]


def test_load_config_invalid_yaml_falls_back_to_defaults(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text("fetch: [", encoding="utf-8")

    config = load_config(str(config_file))
    assert config["fetch"]["count"] == DEFAULT_CONFIG["fetch"]["count"]
    assert config["filter"]["mode"] == DEFAULT_CONFIG["filter"]["mode"]


def test_load_config_does_not_mutate_defaults(tmp_path: Path) -> None:
    config = load_config(str(tmp_path / "missing-config.yaml"))
    config["filter"]["weights"]["likes"] = 999
    assert DEFAULT_CONFIG["filter"]["weights"]["likes"] == 1.0


def test_confirm_config_default(tmp_path: Path) -> None:
    """confirm defaults to True."""
    config = load_config(str(tmp_path / "missing-config.yaml"))
    assert config["confirm"] is True


def test_confirm_config_override(tmp_path: Path) -> None:
    """confirm: false loads correctly."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("confirm: false\n", encoding="utf-8")
    config = load_config(str(config_file))
    assert config["confirm"] is False
