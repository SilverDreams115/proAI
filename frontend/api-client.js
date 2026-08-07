function buildHeaders(extraHeaders = {}) {
  return {...extraHeaders};
}

async function safeFetch(path, options = {}) {
  try {
    const response = await fetch(`${apiBase}${path}`, {
      headers: buildHeaders(),
    });
    if (!response.ok) {
      const detail = await response.text();
      const message = `GET ${path}: ${detail || response.status}`;
      if (response.status === 401) {
        state.authenticated = false;
        state.authMethod = null;
        state.authStatusMessage = "";
        state.authErrorMessage = "Tu sesión expiró. Entra de nuevo para seguir.";
      }
      if (!options.optional) {
        state.lastError = message;
      }
      console.error(message);
      return null;
    }
    if (!options.optional) {
      state.lastError = null;
    }
    return await response.json();
  } catch (error) {
    const message = `GET ${path}: ${error instanceof Error ? error.message : "network error"}`;
    if (!options.optional) {
      state.lastError = message;
    }
    console.error(message);
    return null;
  }
}

async function safePost(path, body = null) {
  try {
    const response = await fetch(`${apiBase}${path}`, {
      method: "POST",
      headers: buildHeaders({"Content-Type": "application/json"}),
      body: body ? JSON.stringify(body) : null,
    });
    if (!response.ok) {
      const detail = await response.text();
      if (response.status === 401) {
        state.authenticated = false;
        state.authMethod = null;
        state.authStatusMessage = "";
        state.authErrorMessage = "Tu sesión expiró. Entra de nuevo para seguir.";
      }
      state.lastError = `POST ${path}: ${detail || response.status}`;
      console.error(state.lastError);
      return null;
    }
    state.lastError = null;
    return await response.json();
  } catch (error) {
    state.lastError = `POST ${path}: ${error instanceof Error ? error.message : "network error"}`;
    console.error(state.lastError);
    return null;
  }
}

async function checkSession() {
  try {
    const response = await fetch(`${apiBase}/auth/session`, {
      headers: buildHeaders(),
    });
    if (!response.ok) {
      state.authenticated = false;
      state.authMethod = null;
      state.authStatusMessage = "";
      return false;
    }
    const payload = await response.json();
    state.authenticated = payload.authenticated === true;
    state.authMethod = payload.method || null;
    return state.authenticated;
  } catch {
    state.authenticated = false;
    state.authMethod = null;
    return false;
  }
}

async function loginWithPassword(password) {
  const response = await fetch(`${apiBase}/auth/login`, {
    method: "POST",
    headers: buildHeaders({"Content-Type": "application/json"}),
    body: JSON.stringify({password}),
  });
  if (!response.ok) {
    state.authenticated = false;
    state.authMethod = null;
    // `lastError` drives the global error banner, which would double up
    // with the gate's own message. The gate says it; the banner stays out.
    state.lastError = null;
    state.authStatusMessage = "";
    state.authErrorMessage = response.status === 429
      ? "Demasiados intentos fallidos. Espera unos minutos antes de reintentar."
      : "Password incorrecto. Vuelve a intentarlo.";
    return false;
  }
  const payload = await response.json();
  state.authenticated = payload.authenticated === true;
  state.authMethod = payload.method || null;
  state.lastError = null;
  state.authStatusMessage = "";
  state.authErrorMessage = state.authenticated
    ? ""
    : "No se pudo iniciar sesión. Revisa la configuración de acceso.";
  return state.authenticated;
}

async function logoutSession() {
  await fetch(`${apiBase}/auth/logout`, {
    method: "POST",
    headers: buildHeaders({"Content-Type": "application/json"}),
  });
  state.authenticated = false;
  state.authMethod = null;
  state.authStatusMessage = "";
  state.authErrorMessage = "";
}
