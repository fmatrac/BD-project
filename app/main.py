"""FastAPI application: HTML pages (Jinja2 MPA) + JSON API.

Routes
------
GET  /                              home, featured movies
GET  /movies                       browse with genre/year filters + search
GET  /movies/{id}                  movie detail + collaborative & content recs
GET  /graph                        visual subgraph page (d3.js)
GET  /api/recommendations/{user_id} JSON recommendations for a user
GET  /api/graph/{movie_id}         JSON subgraph for the /graph page
POST /api/rate                     rate a movie (form or JSON)

Pages share the chosen user via a `user_id` query param so personalization
works without authentication (requirement: simple user dropdown).
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app import queries
from app.db import close_driver

BASE_DIR = os.path.dirname(__file__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    close_driver()  # release the Neo4j driver on shutdown


app = FastAPI(title="Movie Recommender (Neo4j)", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def _users():
    """Users for the dropdown; cached-ish call kept simple for the demo."""
    return queries.list_users()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/")
def home(request: Request, user_id: int | None = None):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "featured": queries.featured_movies(limit=12),
            "users": _users(),
            "user_id": user_id,
            # If a user is selected, show their personalized recommendations.
            "recommendations": queries.recommend_for_user(user_id) if user_id else [],
        },
    )


@app.get("/movies")
def browse(
    request: Request,
    genre: str | None = Query(None),
    year: int | None = Query(None),
    q: str | None = Query(None),
    page: int = Query(1, ge=1),
    user_id: int | None = None,
):
    per_page = 24
    if q:
        movies = queries.search_movies(q)
    else:
        movies = queries.browse_movies(
            genre=genre, year=year, skip=(page - 1) * per_page, limit=per_page
        )
    return templates.TemplateResponse(
        "movies.html",
        {
            "request": request,
            "movies": movies,
            "genres": queries.list_genres(),
            "years": queries.list_years(),
            "users": _users(),
            "user_id": user_id,
            "selected_genre": genre,
            "selected_year": year,
            "q": q,
            "page": page,
        },
    )


@app.get("/movies/{movie_id}")
def movie_detail(request: Request, movie_id: int, user_id: int | None = None):
    movie = queries.movie_detail(movie_id)
    if movie is None:
        return templates.TemplateResponse(
            "not_found.html", {"request": request, "users": _users(), "user_id": user_id},
            status_code=404,
        )
    return templates.TemplateResponse(
        "movie_detail.html",
        {
            "request": request,
            "movie": movie,
            "collaborative": queries.collaborative_for_movie(movie_id),
            "content_based": queries.content_based_for_movie(movie_id),
            "users": _users(),
            "user_id": user_id,
            "your_rating": queries.user_rating_for_movie(user_id, movie_id) if user_id else None,
        },
    )


@app.get("/graph")
def graph_page(request: Request, movie_id: int | None = None, user_id: int | None = None):
    # Default to a well-connected movie so the page is never empty.
    if movie_id is None:
        featured = queries.featured_movies(limit=1)
        movie_id = featured[0]["id"] if featured else None
    return templates.TemplateResponse(
        "graph.html",
        {
            "request": request,
            "movie_id": movie_id,
            "users": _users(),
            "user_id": user_id,
        },
    )


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

@app.get("/api/recommendations/{user_id}")
def api_recommendations(user_id: int, limit: int = 12):
    """User-based collaborative-filtering recommendations as JSON."""
    return {"user_id": user_id, "recommendations": queries.recommend_for_user(user_id, limit)}


@app.get("/api/graph/{movie_id}")
def api_graph(movie_id: int):
    """Node/link JSON for the d3 subgraph visualization."""
    return queries.movie_subgraph(movie_id)


class RatePayload(BaseModel):
    user_id: int
    movie_id: int
    rating: float


@app.post("/api/rate")
async def api_rate(request: Request):
    """Rate a movie. Accepts a JSON body or an HTML form post.

    Form posts (from the detail page) redirect back to the movie; JSON posts
    get a JSON response.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        payload = RatePayload(**body)
        result = queries.rate_movie(payload.user_id, payload.movie_id, payload.rating)
        if result is None:
            return JSONResponse({"error": "user or movie not found"}, status_code=404)
        return {"status": "ok", "rating": result["rating"]}

    # HTML form path
    form = await request.form()
    user_id = int(form["user_id"])
    movie_id = int(form["movie_id"])
    rating = float(form["rating"])
    queries.rate_movie(user_id, movie_id, rating)
    return RedirectResponse(url=f"/movies/{movie_id}?user_id={user_id}", status_code=303)
