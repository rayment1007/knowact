# auth/

The authentication layer (task 5.2). Authentication uses **JWT bearer tokens**
stored client-side (localStorage) and sent via the `Authorization: Bearer <token>`
header (see `src/api/client.ts`). This is NOT cookie-based auth.

- `AuthProvider` — owns the session. On load/refresh it restores the session by
  reading the stored token and calling `GET /api/auth/me`; a 401 clears the
  token and yields an unauthenticated state. Registers the client's bearer-token
  provider and a global 401 handler.
- `useAuth` — hook exposing `{ status, isLoading, isAuthenticated, user,
  organization, login, logout }`.
- `ProtectedRoute` — redirects unauthenticated users to `/login` (shows a
  loading state while the session is being restored).
- `token.ts` — `getStoredToken` / `setStoredToken` / `clearStoredToken`.

`login(token)` stores an already-obtained access token and loads the current
user + organization. `logout()` clears the token and resets to unauthenticated.
The login endpoint call itself lives in `src/api/auth.ts` (`authApi.login`).
