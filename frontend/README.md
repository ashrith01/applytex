# ApplyTeX ATS Web UI

Next.js frontend for the local ApplyTeX ATS FastAPI backend.

## Development

```bash
# From repo root — start API first
uv run applytex-api

# In this directory
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

Set `NEXT_PUBLIC_API_BASE` if the API is not on `http://127.0.0.1:8000`.

## Scripts

- `npm run dev` — development server on port 3000
- `npm run build` — production build
- `npm run lint` — ESLint
- `npm run typecheck` — TypeScript check

## API types

Types in `src/lib/api/types.ts` mirror FastAPI Pydantic models. Regenerate from OpenAPI when backend contracts change:

```bash
curl -s http://127.0.0.1:8000/openapi.json -o /tmp/openapi.json
npx openapi-typescript /tmp/openapi.json -o src/lib/api/generated.ts
```
