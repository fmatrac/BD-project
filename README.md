# MovieGraph — Movie Recommendation System (Neo4j + FastAPI)

A movie recommendation web app built on a **Neo4j** graph database with a
**Python / FastAPI** backend and a server-rendered (Jinja2 + vanilla JS)
frontend. It uses the **MovieLens "small"** dataset and demonstrates two kinds
of recommendation directly in Cypher:

- **Collaborative filtering** — "users who rated this similarly also liked…"
- **Content-based** — similar movies by shared genres and actors

> Academic documentation (UML diagrams, graph structure, deployment notes) is in
> [`docs/dokumentacja.md`](docs/dokumentacja.md) (in Polish).

---

## Features

| Feature | Where |
| --- | --- |
| Browse movies, filter by genre and year | `GET /movies` |
| Search by title | `GET /movies?q=...` |
| Movie detail: cast, director, genres, average rating | `GET /movies/{id}` |
| Collaborative-filtering recommendations | movie detail + `GET /api/recommendations/{user_id}` |
| Content-based recommendations (genre + actors) | movie detail |
| Simple user selection (dropdown, no auth) for personalization | top bar |
| Rate a movie | `POST /api/rate` |
| Visual subgraph (d3.js) | `GET /graph` + `GET /api/graph/{movie_id}` |

---

## Graph schema

**Nodes**

| Label | Properties |
| --- | --- |
| `Movie` | `id` (int, unique), `title`, `year`, `plot` |
| `Person` | `id` (string, unique), `name` — actors and directors |
| `Genre` | `name` (unique) |
| `User` | `id` (int, unique), `name` |

**Relationships**

| Pattern | Properties |
| --- | --- |
| `(User)-[:RATED]->(Movie)` | `rating` (float 0.5–5.0), `timestamp` |
| `(Movie)-[:IN_GENRE]->(Genre)` | — |
| `(Person)-[:ACTED_IN]->(Movie)` | — |
| `(Person)-[:DIRECTED]->(Movie)` | — |

**Constraints** (created by the seed script — they also add the indexes that
make `MERGE` fast):

```cypher
CREATE CONSTRAINT movie_id   IF NOT EXISTS FOR (m:Movie)  REQUIRE m.id IS UNIQUE;
CREATE CONSTRAINT person_id  IF NOT EXISTS FOR (p:Person) REQUIRE p.id IS UNIQUE;
CREATE CONSTRAINT genre_name IF NOT EXISTS FOR (g:Genre)  REQUIRE g.name IS UNIQUE;
CREATE CONSTRAINT user_id    IF NOT EXISTS FOR (u:User)   REQUIRE u.id IS UNIQUE;
```

```
(User) ──RATED{rating}──► (Movie) ──IN_GENRE──► (Genre)
                            ▲   ▲
              ACTED_IN ─────┘   └───── DIRECTED
                 (Person)         (Person)
```

> **Data note.** MovieLens "small" contains movies, genres and ratings only — it
> has **no cast, director or plot**. `Person` nodes and `ACTED_IN`/`DIRECTED`
> relationships (and `Movie.plot`) are added by an **optional** enrichment step
> using the free [TMDB](https://www.themoviedb.org/) API (see below). Without a
> TMDB key the app still works fully for browse, search, detail (genres +
> average rating), collaborative filtering and content-based-by-genre; only the
> actor-based part of content-based recs needs the enrichment.

---

## Setup

### 1. Start Neo4j

**Option A — Docker (easiest):**

```bash
docker compose up -d
# Browser UI at http://localhost:7474  (user: neo4j, password: password)
```

**Option B — AuraDB / local install:** create a database and note its Bolt URI,
user and password.

### 2. Configure environment

```bash
cp .env.example .env
# edit .env if your Neo4j URI / password differ
```

`.env` keys:

```
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
NEO4J_DATABASE=neo4j
TMDB_API_KEY=            # optional, enables cast/director/plot
```

### 3. Install Python dependencies

```bash
python3 -m venv venv          # if you don't already have one
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Seed the database

The seed script downloads the MovieLens "small" dataset automatically (into
`./data/`) on first run, then loads it into Neo4j.

```bash
python -m seed.seed              # download (if needed) + load movies/ratings
python -m seed.seed --reset      # wipe the graph first, then load
```

If you set `TMDB_API_KEY`, the same command also enriches movies with real
cast, directors and plot (capped with `--enrich-limit`, default 500 movies to
keep API usage reasonable):

```bash
python -m seed.seed --enrich-limit 300
```

### 5. Run the app

```bash
uvicorn app.main:app --reload
# open http://127.0.0.1:8000
```

---

## How the recommendations work (Cypher)

All queries live in [`app/queries.py`](app/queries.py), each with a comment
explaining it. The two recommendation cores:

**User-based collaborative filtering** — find like-minded users, then recommend
their highly-rated unseen movies, weighted by how like-minded each neighbour is:

```cypher
MATCH (u:User {id: $userId})-[r1:RATED]->(m:Movie)<-[r2:RATED]-(other:User)
WHERE u <> other AND abs(r1.rating - r2.rating) <= 1.0
WITH u, other, count(*) AS overlap
ORDER BY overlap DESC LIMIT $neighbours
MATCH (other)-[r3:RATED]->(rec:Movie)
WHERE r3.rating >= 4 AND NOT EXISTS { (u)-[:RATED]->(rec) }
RETURN rec.id, rec.title, sum(overlap * r3.rating) AS score
ORDER BY score DESC LIMIT $limit
```

**Content-based** — candidates that share a genre or actor, scored with each
shared actor worth 3× a shared genre:

```cypher
MATCH (m:Movie {id: $id})
CALL {
  WITH m MATCH (m)-[:IN_GENRE]->(:Genre)<-[:IN_GENRE]-(rec:Movie) WHERE rec <> m RETURN rec
  UNION
  WITH m MATCH (m)<-[:ACTED_IN]-(:Person)-[:ACTED_IN]->(rec:Movie) WHERE rec <> m RETURN rec
}
WITH m, rec,
     size([(m)-[:IN_GENRE]->(g)<-[:IN_GENRE]-(rec) | g]) AS genreOverlap,
     size([(m)<-[:ACTED_IN]-(p)-[:ACTED_IN]->(rec) | p]) AS actorOverlap
WITH rec, genreOverlap + 3 * actorOverlap AS score
WHERE score > 0
RETURN rec.id, rec.title, score ORDER BY score DESC LIMIT $limit
```

---

## Project layout

```
app/
  main.py        FastAPI routes (pages + JSON API)
  db.py          Neo4j driver + run_query helper
  queries.py     all Cypher queries, documented
  templates/     Jinja2 pages (base, index, movies, movie_detail, graph)
  static/        style.css, app.js, graph.js (d3)
seed/
  seed.py        downloads MovieLens + loads it into Neo4j
docs/
  dokumentacja.md  UML diagrams, schema, deployment (Polish)
docker-compose.yml Neo4j for local dev
requirements.txt
.env.example
```

---

## Endpoints reference

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/` | Home, featured movies (+ recs if a user is selected) |
| GET | `/movies` | Browse with `genre`, `year`, `q`, `page` filters |
| GET | `/movies/{id}` | Movie detail + both recommendation sets |
| GET | `/graph` | Subgraph visualization page (d3) |
| GET | `/api/recommendations/{user_id}` | JSON: collaborative recs for a user |
| GET | `/api/graph/{movie_id}` | JSON: node/link subgraph |
| POST | `/api/rate` | Upsert a `RATED` relationship (JSON or form) |
