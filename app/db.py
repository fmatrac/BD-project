"""Neo4j connection layer.

One shared driver for the whole process (the driver is thread-safe and manages
its own connection pool). Every read/write goes through `run_query`, which opens
a short-lived session, runs one Cypher statement and returns plain dicts so the
rest of the app never touches Neo4j record objects.
"""

import os

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

_driver = None


def get_driver():
    """Return the process-wide driver, creating it on first use."""
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
    return _driver


def run_query(cypher: str, params: dict | None = None) -> list[dict]:
    """Run one Cypher statement and return the rows as a list of dicts."""
    driver = get_driver()
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(cypher, params or {})
        return [record.data() for record in result]


def close_driver():
    """Close the driver on application shutdown."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
