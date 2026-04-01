import typer

from cli.dashboard import render_dashboard

app = typer.Typer(help="BINFIN CLI")


@app.command()
def dashboard() -> None:
    render_dashboard()


if __name__ == "__main__":
    app()
