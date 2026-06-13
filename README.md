# MovieGraph — System rekomendacji filmów (Neo4j + FastAPI)

Aplikacja webowa rekomendująca filmy, zbudowana na grafowej bazie **Neo4j**,
backendzie w **Pythonie (FastAPI)** i widokach serwerowych (Jinja2 + vanilla JS,
aplikacja typu MPA). Korzysta ze zbioru **MovieLens „small"** i pokazuje dwa
typy rekomendacji realizowane bezpośrednio w języku Cypher:

- **Kolaboracyjna** — „użytkownicy, którzy ocenili ten film podobnie, polubili też…"
- **Treściowa** — podobne filmy po wspólnych gatunkach i aktorach

> Pełna dokumentacja akademicka (diagramy UML, struktura grafu, wdrożenie) jest
> w [`docs/dokumentacja.md`](docs/dokumentacja.md).

---

## Funkcjonalności

| Funkcjonalność | Gdzie |
| --- | --- |
| Przeglądanie filmów, filtrowanie po gatunku i roku | `GET /movies` |
| Wyszukiwanie po tytule | `GET /movies?q=...` |
| Szczegóły filmu: obsada, reżyser, gatunki, średnia ocena | `GET /movies/{id}` |
| Rekomendacje kolaboracyjne | strona szczegółów + `GET /api/recommendations/{user_id}` |
| Rekomendacje treściowe (gatunki + aktorzy) | strona szczegółów |
| Prosty wybór użytkownika (dropdown, bez logowania) — personalizacja | górny pasek |
| Ocenianie filmu | `POST /api/rate` |
| Wizualizacja podgrafu (d3.js) | `GET /graph` + `GET /api/graph/{movie_id}` |

---

## Schemat grafu

**Węzły**

| Etykieta | Właściwości |
| --- | --- |
| `Movie` | `id` (int, unikalne), `title`, `year`, `plot` |
| `Person` | `id` (string, unikalne), `name` — aktorzy i reżyserzy |
| `Genre` | `name` (unikalne) |
| `User` | `id` (int, unikalne), `name` |

**Relacje**

| Wzorzec | Właściwości |
| --- | --- |
| `(User)-[:RATED]->(Movie)` | `rating` (float 0.5–5.0), `timestamp` |
| `(Movie)-[:IN_GENRE]->(Genre)` | — |
| `(Person)-[:ACTED_IN]->(Movie)` | — |
| `(Person)-[:DIRECTED]->(Movie)` | — |

**Ograniczenia (constraints)** — gwarantują unikalność kluczy i jednocześnie
tworzą indeksy, dzięki czemu `MERGE` przy ładowaniu danych jest szybki:

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

> **Uwaga o danych.** MovieLens „small" zawiera tylko filmy, gatunki i oceny —
> **nie ma obsady, reżyserów ani opisów**. Węzły `Person` oraz relacje
> `ACTED_IN`/`DIRECTED` (i pole `Movie.plot`) dodaje **opcjonalny** krok
> wzbogacania danych z darmowego API [TMDB](https://www.themoviedb.org/),
> wykorzystując `tmdbId` z `links.csv`. Bez klucza TMDB aplikacja w pełni
> działa dla przeglądania, wyszukiwania, szczegółów (gatunki + średnia ocena),
> filtrowania kolaboracyjnego i treściowego po gatunkach; tylko część
> rekomendacji treściowej oparta o aktorów potrzebuje wzbogacenia danych.

---

## Instalacja i uruchomienie

### 1. Uruchom Neo4j

**Opcja A — Docker (najprościej):**

```bash
docker compose up -d
# Panel: http://localhost:7474  (użytkownik: neo4j, hasło: password)
```

**Opcja B — AuraDB / lokalna instalacja:** załóż bazę i zanotuj URI Bolt,
użytkownika i hasło.

### 2. Konfiguracja środowiska

```bash
cp .env.example .env
# Edytuj .env, jeśli URI / hasło do Neo4j są inne
```

Klucze w `.env`:

```
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
NEO4J_DATABASE=neo4j
TMDB_API_KEY=            # opcjonalne, włącza obsadę/reżyserów/opisy
```

### 3. Instalacja zależności Pythona

```bash
python3 -m venv venv          # jeśli jeszcze nie masz
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Załadowanie danych

Skrypt seed automatycznie pobiera zbiór MovieLens „small" (do `./data/`) przy
pierwszym uruchomieniu, a następnie ładuje go do Neo4j.

```bash
python -m seed.seed              # pobierz (jeśli trzeba) + załaduj filmy/oceny
python -m seed.seed --reset      # najpierw wyczyść graf, potem załaduj
```

Jeśli ustawisz `TMDB_API_KEY`, ta sama komenda dodatkowo wzbogaci filmy o
prawdziwą obsadę, reżyserów i opis (z limitem `--enrich-limit`, domyślnie 500
filmów, żeby nie obciążać API):

```bash
python -m seed.seed --enrich-limit 300
```

### 5. Uruchomienie aplikacji

```bash
uvicorn app.main:app --reload
# otwórz http://127.0.0.1:8000
```

---

## Jak działają rekomendacje (Cypher)

Wszystkie zapytania są w [`app/queries.py`](app/queries.py), każde z
komentarzem. Dwa kluczowe zapytania rekomendacyjne:

**Kolaboracyjne dla użytkownika** — znajdź podobnych użytkowników, potem
poleć ich wysoko ocenione filmy, których dany użytkownik jeszcze nie widział,
ważąc wynik tym, jak bardzo podobny jest każdy „sąsiad":

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

**Treściowe** — kandydaci dzielący gatunek lub aktora, punktowani z wagą:
każdy wspólny aktor = 3× wspólny gatunek:

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

## Struktura projektu

```
app/
  main.py        trasy FastAPI (strony + JSON API)
  db.py          sterownik Neo4j + pomocnik run_query
  queries.py     wszystkie zapytania Cypher z dokumentacją
  templates/     widoki Jinja2 (base, index, movies, movie_detail, graph)
  static/        style.css, app.js, graph.js (d3)
seed/
  seed.py        pobiera MovieLens i ładuje do Neo4j
docs/
  dokumentacja.md  diagramy UML, schemat, opis wdrożenia
docker-compose.yml Neo4j do lokalnego developmentu
requirements.txt
.env.example
```

---

## Lista endpointów

| Metoda | Ścieżka | Zwraca |
| --- | --- | --- |
| GET | `/` | Strona główna, wyróżnione filmy (+ rekomendacje, jeśli wybrany użytkownik) |
| GET | `/movies` | Przeglądanie z filtrami `genre`, `year`, `q`, `page` |
| GET | `/movies/{id}` | Szczegóły filmu + obie listy rekomendacji |
| GET | `/graph` | Strona z wizualizacją podgrafu (d3) |
| GET | `/api/recommendations/{user_id}` | JSON: rekomendacje kolaboracyjne dla użytkownika |
| GET | `/api/graph/{movie_id}` | JSON: podgraf w formacie węzły/krawędzie |
| POST | `/api/rate` | Zapisuje/aktualizuje relację `RATED` (JSON lub formularz) |
