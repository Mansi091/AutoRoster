# AutoRoster frontend

This directory contains the React and Vite supervisor interface. For full application setup, backend configuration, Clerk keys, and Supabase migration steps, see the [root README](../README.md).

~~~powershell
npm install
Copy-Item .env.example .env
npm run dev
~~~

Set VITE_CLERK_PUBLISHABLE_KEY in frontend/.env. The Vite development server runs on http://localhost:5173 and proxies /api requests to FastAPI on port 5000.
