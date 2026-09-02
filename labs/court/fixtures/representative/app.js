// Representative Agent target: results arrive by fetch, the button fetches
// a status document and appends it. Same-origin only, no timers, no storage.
(() => {
  const status = document.getElementById("status");
  const results = document.getElementById("results");
  const button = document.getElementById("continue");
  fetch("data.json")
    .then((response) => response.json())
    .then((items) => {
      for (const item of items) {
        const li = document.createElement("li");
        const link = document.createElement("a");
        link.setAttribute("href", item.href);
        link.textContent = item.title;
        li.append(link);
        results.append(li);
      }
      status.textContent = items.length + " results";
    })
    .catch((error) => {
      status.textContent = "results failed: " + (error.code || error.message);
    });
  button.addEventListener("click", () => {
    fetch("status.json")
      .then((response) => response.json())
      .then((body) => {
        const note = document.createElement("p");
        note.id = "note";
        note.textContent = body.message;
        document.querySelector("main").append(note);
      })
      .catch((error) => {
        const note = document.createElement("p");
        note.id = "note";
        note.textContent = "status failed: " + (error.code || error.message);
        document.querySelector("main").append(note);
      });
  });
})();
