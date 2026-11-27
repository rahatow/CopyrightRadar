/* "Aa larger" text toggle, remembered per browser. */
(() => {
  const root = document.documentElement;
  try { if (localStorage.getItem("cr-large") === "1") root.classList.add("large"); } catch (e) {}
  document.addEventListener("DOMContentLoaded", () => {
    const b = document.getElementById("larger");
    if (!b) return;
    const sync = () => b.setAttribute("aria-pressed", root.classList.contains("large"));
    sync();
    b.onclick = () => {
      root.classList.toggle("large");
      try { localStorage.setItem("cr-large", root.classList.contains("large") ? "1" : "0"); } catch (e) {}
      sync();
    };
  });
})();
