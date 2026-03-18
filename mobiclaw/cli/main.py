"""MobiClaw CLI root."""
import click


@click.group()
@click.option("--server-url", envvar="MOBICLAW_SERVER_URL", help="Gateway server URL")
@click.option("--api-key", envvar="MOBICLAW_API_KEY", help="API key for auth")
@click.option("--output", "output_fmt", type=click.Choice(["json", "table", "text"]), default="table")
@click.option("--verbose", is_flag=True)
@click.pass_context
def cli(ctx, server_url, api_key, output_fmt, verbose):
    ctx.ensure_object(dict)
    ctx.obj["server_url"] = server_url
    ctx.obj["api_key"] = api_key
    ctx.obj["output_fmt"] = output_fmt
    ctx.obj["verbose"] = verbose


@cli.command()
def health():
    """Health check."""
    click.echo("health (placeholder)")
