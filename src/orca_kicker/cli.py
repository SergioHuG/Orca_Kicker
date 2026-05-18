"""CLI entry point. Stub — extend as needed."""

import click


@click.command()
@click.option(
    "--config",
    default="configs/default.yaml",
    show_default=True,
    help="Path to YAML config file.",
)
def main(config: str) -> None:
    """Orca Kicker — linear motor kick application."""
    from orca_kicker.main import run

    run(config_path=config)


if __name__ == "__main__":
    main()
