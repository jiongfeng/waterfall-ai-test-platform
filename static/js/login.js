(() => {
  "use strict";

  const loginForm = document.getElementById("loginForm");
  const loginSubmit = document.getElementById("loginSubmit");
  const loginError = document.getElementById("loginError");
  const loginUsername = document.getElementById("loginUsername");
  const loginPassword = document.getElementById("loginPassword");
  const csrfToken =
    document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";

  function showLoginError(message) {
    loginError.textContent = message || "";
    loginError.classList.toggle("hidden", !message);
  }

  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showLoginError("");
    loginSubmit.disabled = true;
    loginSubmit.textContent = "Signing in…";
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
        },
        body: JSON.stringify({
          username: loginUsername.value.trim(),
          password: loginPassword.value,
        }),
      });
      const data = await response
        .json()
        .catch(() => ({ error: "The server did not return JSON." }));
      if (!response.ok) {
        throw new Error(data.error || `Sign-in failed: ${response.status}`);
      }
      window.location.href = "/";
    } catch (error) {
      showLoginError(error.message || "Sign-in failed.");
      loginPassword.focus();
    } finally {
      loginSubmit.disabled = false;
      loginSubmit.textContent = "Sign in";
    }
  });
})();
