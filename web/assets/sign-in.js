/* Sign-in.
 *
 * Three rules this file follows, each because the alternative is a real defect:
 *
 *   1. The failure message never distinguishes a wrong password from a missing
 *      account. Telling a caller which it was turns this form into a way to
 *      enumerate who has an account.
 *   2. The reason for a signed-out session is SHOWN ("signed out after idle
 *      timeout") because an analyst needs to know what happened, and the reason
 *      reveals nothing a caller did not already know.
 *   3. Nothing is remembered locally except the theme. A password manager prompt
 *      is the operator's choice; a token in localStorage is ours, and on a shared
 *      workstation that is somebody else's credential.
 */

"use strict";

const THEME_KEY = "netra-theme";

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.getElementById("themeNote").textContent = `${theme} theme`;
}

(async function main() {
  // ---- theme, and the build identity --------------------------------------
  let theme = "light";
  try {
    theme = localStorage.getItem(THEME_KEY)
      || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  } catch (error) { /* private browsing: the default is fine */ }
  applyTheme(theme);

  // The version endpoint is public on purpose: it carries no data, and it is what
  // a support call needs from somebody who cannot sign in.
  try {
    const build = await (await fetch("/api/auth/version")).json();
    document.getElementById("build").textContent =
      `${build.product} · engine ${build.engine_version} · contract ${build.schema_version}`;
  } catch (error) { /* the sign-in form does not depend on this */ }

  // ---- why we are here ----------------------------------------------------
  const reason = new URLSearchParams(location.search).get("reason");
  if (reason && reason !== "no session") {
    const box = document.getElementById("reason");
    box.hidden = false;
    const friendly = {
      "signed out after idle timeout":
        "Signed out after a period of inactivity. Sign in to continue.",
      "session expired":
        "This session reached its maximum age. Sign in to continue.",
      "unknown session": "That session is no longer valid. Sign in to continue.",
    }[reason] || reason;
    box.innerHTML = `<div class="ic">
      <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="9"/><path d="M12 8v4"/><path d="M12 16h.01"/></svg></div>
      <div>${friendly}</div>`;
  }

  // ---- sign in ------------------------------------------------------------
  const form = document.getElementById("signinForm");
  const submit = document.getElementById("submit");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    submit.textContent = "Signing in…";
    try {
      const response = await fetch("/api/auth/sign-in", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: document.getElementById("username").value.trim(),
          password: document.getElementById("password").value,
        }),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        showError(detail.detail || "those credentials were not accepted");
        return;
      }
      // The session and CSRF cookies are set by the response; the CSRF value is
      // kept in memory for the tab's lifetime rather than in storage, so it dies
      // with the tab.
      const body = await response.json();
      sessionStorage.setItem("netra-csrf", body.csrf);
      location.href = "app.html";
    } catch (error) {
      showError("The server did not answer. It may have stopped.");
    } finally {
      submit.disabled = false;
      submit.textContent = "Sign in";
    }
  });

  function showError(message) {
    const box = document.getElementById("reason");
    box.hidden = false;
    box.className = "notice bad";
    box.innerHTML = `<div class="ic">
      <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 9v4"/><path d="M12 17h.01"/>
        <path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>
      </svg></div><div>${message}</div>`;
  }

  // ---- reset token redemption --------------------------------------------
  document.getElementById("resetToggle").addEventListener("click", () => {
    const box = document.getElementById("resetForm");
    box.hidden = !box.hidden;
  });

  document.getElementById("resetForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const response = await fetch("/api/auth/redeem-reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: document.getElementById("username").value.trim(),
          token: document.getElementById("token").value.trim(),
          new_password: document.getElementById("newPassword").value,
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        showError(body.detail || "that reset token was not accepted");
        return;
      }
      const box = document.getElementById("reason");
      box.hidden = false;
      box.className = "notice ok";
      box.innerHTML = "<div>Password set. Sign in with the new password.</div>";
      document.getElementById("resetForm").hidden = true;
    } catch (error) {
      showError("The server did not answer.");
    }
  });

  // The keyboard is how this screen is actually used, so the theme toggle is on
  // a key rather than only a button somewhere else.
  document.addEventListener("keydown", (event) => {
    if (event.key === "F2") {
      theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      try { localStorage.setItem(THEME_KEY, theme); } catch (error) { /* ignore */ }
      applyTheme(theme);
    }
  });
})();
