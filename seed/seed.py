"""Seed Neo4j with the MovieLens "small" dataset.

What it loads
-------------
- movies.csv  -> (:Movie) nodes (id, title, year) + (:Genre) + IN_GENRE
- ratings.csv -> (:User) nodes + (:User)-[:RATED]->(:Movie)

MovieLens "small" has NO cast, director or plot. If TMDB_API_KEY is set in the
environment, the optional enrichment step pulls real actors, directors and plot
from TMDB using the tmdbId in links.csv and creates (:Person) nodes plus
ACTED_IN / DIRECTED relationships. Without a key the app still works for browse,
search, detail, collaborative filtering and content-based-by-genre.

Run it
------
    python -m seed.seed                 # download dataset if missing, then load
    python -m seed.seed --reset         # wipe the graph first
    python -m seed.seed --enrich-limit 300   # cap TMDB calls (default 500)

The data lives in ./data/ml-latest-small/ and is downloaded automatically from
GroupLens if not already present.
"""

import argparse
import csv
import io
import os
import re
import sys
import time
import zipfile

import requests
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()

DATA_URL = "https://files.grouplens.org/datasets/movielens/ml-latest-small.zip"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "ml-latest-small")
DATA_DIR = os.path.abspath(DATA_DIR)

# "Toy Story (1995)" -> title "Toy Story", year 1995
YEAR_RE = re.compile(r"^(.*?)\s*\((\d{4})\)\s*$")

BATCH = 2000  # rows per write transaction


# ---------------------------------------------------------------------------
# Schema: unique constraints (also create backing indexes, so MERGE is fast)
# ---------------------------------------------------------------------------
CONSTRAINTS = [
    "CREATE CONSTRAINT movie_id  IF NOT EXISTS FOR (m:Movie)  REQUIRE m.id IS UNIQUE",
    "CREATE CONSTRAINT person_id IF NOT EXISTS FOR (p:Person) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT genre_name IF NOT EXISTS FOR (g:Genre) REQUIRE g.name IS UNIQUE",
    "CREATE CONSTRAINT user_id   IF NOT EXISTS FOR (u:User)  REQUIRE u.id IS UNIQUE",
]

LOAD_MOVIES = """
UNWIND $rows AS row
MERGE (m:Movie {id: row.id})
SET m.title = row.title, m.year = row.year
WITH m, row
UNWIND row.genres AS gname
MERGE (g:Genre {name: gname})
MERGE (m)-[:IN_GENRE]->(g)
"""

LOAD_RATINGS = """
UNWIND $rows AS row
MERGE (u:User {id: row.userId})
ON CREATE SET u.name = 'User ' + toString(row.userId)
MERGE (m:Movie {id: row.movieId})
MERGE (u)-[r:RATED]->(m)
SET r.rating = row.rating, r.timestamp = row.timestamp
"""

LOAD_PEOPLE = """
UNWIND $rows AS row
MATCH (m:Movie {id: row.movieId})
SET m.plot = row.plot
WITH m, row
UNWIND row.actors AS a
MERGE (p:Person {id: a.id})
ON CREATE SET p.name = a.name
MERGE (p)-[:ACTED_IN]->(m)
WITH m, row
UNWIND row.directors AS d
MERGE (p:Person {id: d.id})
ON CREATE SET p.name = d.name
MERGE (p)-[:DIRECTED]->(m)
"""


def chunked(rows, size=BATCH):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def ensure_dataset():
    """Download + unzip ml-latest-small into ./data if it is not there yet."""
    movies = os.path.join(DATA_DIR, "movies.csv")
    if os.path.exists(movies):
        print(f"Dataset found at {DATA_DIR}")
        return
    print(f"Dataset not found, downloading from {DATA_URL} ...")
    resp = requests.get(DATA_URL, timeout=120)
    resp.raise_for_status()
    target = os.path.abspath(os.path.join(DATA_DIR, "..", ".."))
    os.makedirs(os.path.join(target, "data"), exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        zf.extractall(os.path.join(target, "data"))
    print(f"Extracted to {DATA_DIR}")


def parse_movies():
    """Read movies.csv -> list of {id, title, year, genres[]}."""
    rows = []
    path = os.path.join(DATA_DIR, "movies.csv")
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw = row["title"].strip()
            m = YEAR_RE.match(raw)
            if m:
                title, year = m.group(1).strip(), int(m.group(2))
            else:
                title, year = raw, None
            genres = [g for g in row["genres"].split("|") if g and g != "(no genres listed)"]
            rows.append(
                {"id": int(row["movieId"]), "title": title, "year": year, "genres": genres}
            )
    return rows


def parse_ratings():
    """Read ratings.csv -> list of {userId, movieId, rating, timestamp}."""
    rows = []
    path = os.path.join(DATA_DIR, "ratings.csv")
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(
                {
                    "userId": int(row["userId"]),
                    "movieId": int(row["movieId"]),
                    "rating": float(row["rating"]),
                    "timestamp": int(row["timestamp"]),
                }
            )
    return rows


def parse_links():
    """Read links.csv -> {movieId: tmdbId} for movies that have a tmdbId."""
    mapping = {}
    path = os.path.join(DATA_DIR, "links.csv")
    if not os.path.exists(path):
        return mapping
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tmdb = row.get("tmdbId", "").strip()
            if tmdb:
                mapping[int(row["movieId"])] = int(tmdb)
    return mapping


def fetch_tmdb_credits(tmdb_id, session):
    """Return (plot, actors[], directors[]) for one TMDB movie id, or None."""
    base = "https://api.themoviedb.org/3/movie"
    try:
        details = session.get(
            f"{base}/{tmdb_id}", params={"api_key": TMDB_API_KEY}, timeout=20
        )
        if details.status_code != 200:
            return None
        plot = details.json().get("overview") or None

        credits = session.get(
            f"{base}/{tmdb_id}/credits", params={"api_key": TMDB_API_KEY}, timeout=20
        )
        if credits.status_code != 200:
            return None
        data = credits.json()
        actors = [
            {"id": f"p{c['id']}", "name": c["name"]}
            for c in data.get("cast", [])[:8]
        ]
        directors = [
            {"id": f"p{c['id']}", "name": c["name"]}
            for c in data.get("crew", [])
            if c.get("job") == "Director"
        ]
        return plot, actors, directors
    except requests.RequestException:
        return None


def write_batches(driver, cypher, rows, label):
    total = 0
    with driver.session(database=NEO4J_DATABASE) as session:
        for batch in chunked(rows):
            session.execute_write(lambda tx: tx.run(cypher, rows=batch).consume())
            total += len(batch)
            print(f"  {label}: {total}/{len(rows)}", end="\r")
    print(f"  {label}: {total}/{len(rows)}   done")


def enrich_people(driver, links, enrich_limit):
    if not TMDB_API_KEY:
        print("TMDB_API_KEY not set -> skipping cast/director/plot enrichment.")
        return
    movie_ids = list(links.keys())[:enrich_limit]
    print(f"Enriching {len(movie_ids)} movies from TMDB (cast/director/plot) ...")
    session_http = requests.Session()
    rows = []
    for i, movie_id in enumerate(movie_ids, 1):
        result = fetch_tmdb_credits(links[movie_id], session_http)
        if result:
            plot, actors, directors = result
            rows.append(
                {"movieId": movie_id, "plot": plot, "actors": actors, "directors": directors}
            )
        if i % 25 == 0:
            print(f"  fetched {i}/{len(movie_ids)}", end="\r")
            time.sleep(0.2)  # be polite to the API
    print(f"  fetched {len(rows)} movies from TMDB        ")
    if rows:
        write_batches(driver, LOAD_PEOPLE, rows, "people")


def main():
    ap = argparse.ArgumentParser(description="Seed Neo4j with MovieLens data.")
    ap.add_argument("--reset", action="store_true", help="delete all nodes first")
    ap.add_argument("--enrich-limit", type=int, default=500,
                    help="max movies to enrich via TMDB (default 500)")
    args = ap.parse_args()

    ensure_dataset()

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001
        print(f"Cannot connect to Neo4j at {NEO4J_URI}: {exc}")
        print("Start Neo4j (e.g. `docker compose up -d`) and check your .env.")
        sys.exit(1)

    with driver.session(database=NEO4J_DATABASE) as session:
        if args.reset:
            print("Resetting graph (DETACH DELETE all nodes) ...")
            session.run("MATCH (n) DETACH DELETE n").consume()
        print("Creating constraints ...")
        for c in CONSTRAINTS:
            session.run(c).consume()

    print("Loading movies + genres ...")
    write_batches(driver, LOAD_MOVIES, parse_movies(), "movies")

    print("Loading users + ratings ...")
    write_batches(driver, LOAD_RATINGS, parse_ratings(), "ratings")

    enrich_people(driver, parse_links(), args.enrich_limit)

    # Final summary so you can confirm the load worked.
    counts = {
        "Movies": "MATCH (m:Movie)  RETURN count(m) AS n",
        "Users": "MATCH (u:User)   RETURN count(u) AS n",
        "Genres": "MATCH (g:Genre)  RETURN count(g) AS n",
        "People": "MATCH (p:Person) RETURN count(p) AS n",
        "Ratings": "MATCH ()-[r:RATED]->() RETURN count(r) AS n",
    }
    with driver.session(database=NEO4J_DATABASE) as session:
        print("\nGraph summary:")
        for label, q in counts.items():
            n = session.run(q).single()["n"]
            print(f"  {label:8}: {n}")

    driver.close()
    print("\nSeed complete.")


if __name__ == "__main__":
    main()
