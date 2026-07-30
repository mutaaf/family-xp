/* Guestbook + retro hit counter, auto-added to every kid page.
   Reads /kids/<kid>/<proj>/ from the URL and talks to the portal API. */
(function () {
  const m = location.pathname.match(/^\/kids\/([^/]+)\/([^/]+)\//);
  if (!m) return;
  const kid = m[1], proj = m[2];

  const box = document.createElement("div");
  box.style.cssText = "max-width:560px;margin:40px auto 20px;padding:16px;" +
    "background:rgba(255,255,255,.85);border:3px solid #1446a0;border-radius:16px;" +
    "font-family:'Comic Sans MS','Chalkboard SE',sans-serif;text-align:left;color:#222";
  document.body.appendChild(box);

  function esc(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  }

  async function load() {
    let d;
    try {
      d = await (await fetch(`/api/guestbook?kid=${kid}&proj=${proj}`)).json();
    } catch (e) { return; }
    if (d.error) return;
    const digits = String(d.visits).padStart(5, "0").split("").map(n =>
      `<span style="display:inline-block;background:#111;color:#3f3;padding:2px 6px;` +
      `margin:0 1px;border-radius:4px;font-family:monospace;font-size:20px">${n}</span>`
    ).join("");
    box.innerHTML = `
      <div style="text-align:center;margin-bottom:10px">
        <div style="font-size:13px;color:#555">👀 people have visited this page</div>
        <div>${digits}</div>
        <div style="font-size:11px;color:#888">(every refresh asks the server again — try it!)</div>
      </div>
      <b>📖 Guestbook</b>
      <div id="gb-list" style="margin:8px 0"></div>
      <form id="gb-form" style="display:flex;gap:6px">
        <input id="gb-msg" maxlength="300" placeholder="Say something nice…"
          style="flex:1;padding:8px;border-radius:10px;border:2px solid #1446a0;font:inherit">
        <button style="padding:8px 14px;border-radius:10px;border:2px solid #1446a0;
          background:#ffd23f;font:inherit;cursor:pointer">Sign ✍️</button>
      </form>`;
    const list = box.querySelector("#gb-list");
    list.innerHTML = (d.entries || []).length
      ? d.entries.map(e =>
          `<div style="padding:6px 8px;margin:4px 0;background:#fffbe6;` +
          `border-radius:8px;border:1px dashed #d0a">` +
          `<b>${esc(e.from)}</b>: ${esc(e.msg)}</div>`).join("")
      : `<div style="color:#888;font-size:13px">No signatures yet — be the first!</div>`;
    box.querySelector("#gb-form").addEventListener("submit", async ev => {
      ev.preventDefault();
      const msg = box.querySelector("#gb-msg").value.trim();
      if (!msg) return;
      await fetch("/api/guestbook", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kid, proj, msg }) });
      load();
    });
  }
  load();
})();
