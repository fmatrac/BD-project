// Force-directed subgraph for the /graph page.
// Fetches node/link JSON from the backend (GET /api/graph/{movie_id}) and draws
// it with d3. The backend builds the data from Cypher, so no Neo4j credentials
// are ever exposed to the browser.

(function () {
  const container = document.getElementById("graph");
  if (!container) return;
  const movieId = container.dataset.movieId;
  if (!movieId || movieId === "None") {
    container.innerHTML = "<p style='padding:20px;color:#9aa0ad'>No movie selected.</p>";
    return;
  }

  fetch(`/api/graph/${movieId}`)
    .then((r) => r.json())
    .then((data) => render(data))
    .catch((e) => {
      container.innerHTML =
        "<p style='padding:20px;color:#f4978e'>Failed to load graph: " + e + "</p>";
    });

  function render(data) {
    if (!data.nodes.length) {
      container.innerHTML = "<p style='padding:20px;color:#9aa0ad'>Empty subgraph.</p>";
      return;
    }
    const width = container.clientWidth;
    const height = container.clientHeight;

    const svg = d3
      .select("#graph")
      .append("svg")
      .attr("width", width)
      .attr("height", height);

    const sim = d3
      .forceSimulation(data.nodes)
      .force("link", d3.forceLink(data.links).id((d) => d.id).distance(90))
      .force("charge", d3.forceManyBody().strength(-260))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collide", d3.forceCollide(28));

    const link = svg
      .append("g")
      .selectAll("line")
      .data(data.links)
      .join("line")
      .attr("class", "glink");

    const edgeLabel = svg
      .append("g")
      .selectAll("text")
      .data(data.links.filter((l) => l.type && l.type.startsWith("RATED")))
      .join("text")
      .attr("class", "gedgelabel")
      .text((d) => d.type);

    const node = svg
      .append("g")
      .selectAll("circle")
      .data(data.nodes)
      .join("circle")
      .attr("class", (d) => "node " + d.group)
      .attr("r", (d) => (d.group === "Movie" ? 16 : 9))
      .call(drag(sim));

    node.append("title").text((d) => `${d.group}: ${d.label}`);

    const label = svg
      .append("g")
      .selectAll("text")
      .data(data.nodes)
      .join("text")
      .attr("class", "glabel")
      .attr("dx", 12)
      .attr("dy", 4)
      .text((d) => d.label);

    sim.on("tick", () => {
      link
        .attr("x1", (d) => d.source.x)
        .attr("y1", (d) => d.source.y)
        .attr("x2", (d) => d.target.x)
        .attr("y2", (d) => d.target.y);
      edgeLabel
        .attr("x", (d) => (d.source.x + d.target.x) / 2)
        .attr("y", (d) => (d.source.y + d.target.y) / 2);
      node.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
      label.attr("x", (d) => d.x).attr("y", (d) => d.y);
    });
  }

  function drag(sim) {
    return d3
      .drag()
      .on("start", (event, d) => {
        if (!event.active) sim.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on("drag", (event, d) => {
        d.fx = event.x;
        d.fy = event.y;
      })
      .on("end", (event, d) => {
        if (!event.active) sim.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });
  }
})();
