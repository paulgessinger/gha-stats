import asyncio
import datetime
import functools
import os
import re
import urllib.parse
from pathlib import Path
from typing import List, Optional
from datetime import datetime, date, timedelta

import aiohttp
import gidgethub
import typer
from gidgethub.aiohttp import GitHubAPI
import cachetools
import peewee
from aioitertools import more_itertools as mi
import dateutil.parser

from gha_stats import __version__, config
from gha_stats.database import database, Job, Run

cli = typer.Typer()


def make_sync(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(fn(*args, **kwargs))

    return wrapped


@cli.command(help="")
@make_sync
async def collect(
    repo: str,
    token: str = typer.Option(
        config.GH_TOKEN,
        help="Github API token to use. Can be supplied with environment variable GH_TOKEN",
    ),
    since: Optional[datetime] = None,
    skip: bool = False,
):
    cache = cachetools.LRUCache(maxsize=500)
    if since is not None:
        since: date = since.date()

    most_recent = Run.select().order_by(Run.created_at.desc()).limit(1).first()

    async with aiohttp.ClientSession(loop=asyncio.get_event_loop()) as session:
        gh = GitHubAPI(session, __name__, oauth_token=token, cache=cache)
        url = f"/repos/{repo}/actions/runs"
        if since is not None:
            url += f"?created=%3A>{since:%Y-%m-%d}"
        elif skip and most_recent is not None:
            #  print(most_recent.created_at, repr(most_recent.created_at))
            created_at = dateutil.parser.parse(most_recent.created_at) - timedelta(
                days=1
            )
            url += f"?created=%3A>={created_at:%Y-%m-%d}"

        async for run in gh.getiter(url, iterable_key="workflow_runs"):
            rows = []

            print(f"- run id: {run['id']} created_at: {run['created_at']}")

            kw = dict(
                created_at=dateutil.parser.parse(run["created_at"]),
                updated_at=dateutil.parser.parse(run["updated_at"]),
                run_started_at=dateutil.parser.parse(run["run_started_at"]),
                **{
                    k: run[k]
                    for k in [
                        "id",
                        "name",
                        "head_branch",
                        "head_sha",
                        "path",
                        "run_number",
                        "event",
                        "status",
                        "conclusion",
                        "workflow_id",
                        "check_suite_id",
                        "url",
                        "html_url",
                        "run_attempt",
                        "jobs_url",
                    ]
                },
            )

            try:
                run_model = Run.create(**kw)
            except peewee.IntegrityError:
                try:
                    Run.replace(**kw).execute()
                except:
                    print(kw)
                    raise
                continue

            jobs = []

            print("Getting jobs")

            async for job in gh.getiter(run["jobs_url"], iterable_key="jobs"):
                row = {
                    "started_at": dateutil.parser.parse(job["started_at"]),
                    "completed_at": None
                    if job["completed_at"] is None
                    else dateutil.parser.parse(job["completed_at"]),
                    "run": run_model,
                }
                row.update(
                    {
                        k: job[k]
                        for k in [
                            "id",
                            "head_sha",
                            "url",
                            "html_url",
                            "status",
                            "conclusion",
                            "name",
                            "check_run_url",
                        ]
                    }
                )
                jobs.append(row)

                print(f"Inserting {len(jobs)} jobs")

            Job.insert_many(jobs).on_conflict_replace().execute()


@cli.callback()
def main():
    database.init(config.DATABASE_URL)
    database.connect()
    database.create_tables([Job, Run])


main.__doc__ = """
GitHub Actions statistics, version {version}
""".format(
    version=__version__
)
