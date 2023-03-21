from flask import Flask
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from prometheus_client import make_wsgi_app, Gauge, generate_latest, REGISTRY
import sqlite3
import pandas

from gha_stats.database import prepare_database, database

actions_jobs = Gauge("actions_jobs", "Number of jobs by status", labelnames=["status", "name"])
jobs_duration = Gauge("jobs_duration", "Duration of most recent job run", labelnames=["name"])

def create_app():
    app = Flask(__name__)
    app.config.from_prefixed_env()

    @app.get("/")
    def index():
        return "hallo"

    @app.get("/metrics")
    def metrics():
        db_file= app.config["DATABASE_FILE"]
        prepare_database(db_file)

        con = sqlite3.connect(db_file)
        cur = con.cursor()

        query = """
        SELECT r.name || " / " || a.name as fqn, a.status as status, COUNT(*) as count FROM job as a 
        JOIN run as r ON a.run_id = r.id WHERE r.created_at > '2022-01-01 00:00:00+00:00' 
        AND r.conclusion != 'skipped' GROUP BY fqn, a.status ORDER BY r.created_at ASC
        """
        for fqn, status, count in cur.execute(query):
            actions_jobs.labels(status=status, name=fqn).set(count)

        df = pandas.read_sql("""
        SELECT 
        r.name || " / " || j.name as fqn, 
        (JULIANDAY(j.completed_at) - JULIANDAY(j.started_at)) * 24 * 60 * 60 as duration
        FROM job as j
        JOIN run as r ON j.run_id = r.id WHERE r.created_at > '2022-01-01 00:00:00+00:00' 
        AND r.conclusion != 'skipped' AND j.status == 'completed' ORDER BY j.completed_at DESC
        """, con)
        df = df.groupby("fqn").first()

        for fqn, duration in df.itertuples():
            jobs_duration.labels(name=fqn).set(duration)


        return generate_latest(REGISTRY)

    return app
