(() => {
  "use strict";
  const fragment = window.location.hash;
  // First effect: remove the transient secret, including malformed input.
  // Never use storage, analytics, console output, or a service worker here.
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  const fail = () => {
    document.getElementById("entry-status").textContent =
      "Open from the owner launcher again: amplifier-unified open.";
  };
  const form = document.getElementById("browser-entry");
  const params = new URLSearchParams(fragment.slice(1));
  const ticket = params.get("ticket");
  const action = new URL(form.action, window.location.href);
  if (params.size !== 1 || !/^[A-Za-z0-9_-]{43}$/.test(ticket || "") ||
      window.location.protocol !== "https:" ||
      action.origin !== window.location.origin ||
      action.pathname !== "/api/browser-entry/redeem" || action.search || action.hash ||
      !["127.0.0.1", "[::1]", "localhost"].includes(window.location.hostname)) {
    fail();
    return;
  }
  // Same-origin navigation POST carries the signed CSRF field and cookie.
  // The server sets the existing HttpOnly session cookie; never replay.
  form.elements.ticket.value = ticket;
  try {
    form.submit();
  } catch {
    form.elements.ticket.value = "";
    fail();
  }
})();
