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
        state.authStatusMessage = "La sesión no está activa. Ingresa el password.";
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
        state.authStatusMessage = "La sesión no está activa. Ingresa el password.";
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
      state.authStatusMessage = "Ingresa el password para cargar la quiniela.";
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
    state.lastError = "Password incorrecto o autenticación no configurada.";
    state.authStatusMessage = response.status === 429
      ? "Demasiados intentos. Espera unos minutos."
      : "Password incorrecto.";
    return false;
  }
  const payload = await response.json();
  state.authenticated = payload.authenticated === true;
  state.authMethod = payload.method || null;
  state.lastError = null;
  state.authStatusMessage = state.authenticated ? "Sesión activa. Cargando quiniela." : "No se pudo iniciar sesión.";
  return state.authenticated;
}

async function logoutSession() {
  await fetch(`${apiBase}/auth/logout`, {
    method: "POST",
    headers: buildHeaders({"Content-Type": "application/json"}),
  });
  state.authenticated = false;
  state.authMethod = null;
  state.authStatusMessage = "Sesión cerrada.";
}
