// Hermes Dashboard — auto-refresh and small interactions

// Auto-refresh the page every 10 seconds (status + heartbeats)
// Only on pages that have the auto-refresh meta tag
const autoRefresh = document.querySelector('meta[name="auto-refresh"]');
if (autoRefresh && autoRefresh.content === "true") {
  const intervalSec = parseInt(autoRefresh.getAttribute("data-interval") || "10", 10);
  setTimeout(() => {
    window.location.reload();
  }, intervalSec * 1000);
}

// Format timestamps in user's local timezone
document.addEventListener("DOMContentLoaded", () => {
  const timestamps = document.querySelectorAll("[data-timestamp]");
  timestamps.forEach((el) => {
    const ts = el.getAttribute("data-timestamp");
    if (ts) {
      try {
        const date = new Date(ts);
        el.textContent = date.toLocaleString();
        el.title = ts; // show ISO on hover
      } catch (e) {
        // keep original text
      }
    }
  });
});
