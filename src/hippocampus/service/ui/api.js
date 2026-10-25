// Swagger UI for the API tab. It is framed by the dashboard (same origin), so it reads the dashboard's token from the shared
// sessionStorage and adds it to every request to this server; "Try it out" then runs with the access you already have.
"use strict";

const TOKEN_KEY = "hippo-token"; // the same key as app.js

function getToken() { try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (_) { return ""; } }

SwaggerUIBundle({
  url: "/openapi.json",
  dom_id: "#swagger",
  deepLinking: false,
  requestInterceptor: (req) => {
    const token = getToken();
    const target = new URL(req.url, location.origin);
    if (token && target.origin === location.origin) req.headers.Authorization = `Bearer ${token}`; // never sent elsewhere
    return req;
  },
});
