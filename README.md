# ab-verified — Vercel deploy harness

A minimal Vite + React "hello world" used to verify that pushes to `main`
deploy automatically to Vercel.

## Local

```bash
npm install
npm run dev      # http://localhost:5173
npm run build    # outputs to dist/
npm run preview  # serve the production build locally
```

## Deploying

Vercel's Git integration handles this — no CI workflow or secrets needed.

1. Go to <https://vercel.com/new> and import `andrewbruno/ab-verified`.
2. Vercel auto-detects Vite; `vercel.json` pins the framework, build command
   (`npm run build`) and output directory (`dist`) so the detection can't drift.
3. Click **Deploy**.

After that first import, every push to `main` triggers a Production deploy and
every push to any other branch or PR gets its own Preview URL. Nothing in this
repo needs to change to enable it.

## Files

| Path | Purpose |
| --- | --- |
| `index.html` | Vite entry document |
| `src/main.jsx` | React root |
| `src/App.jsx` | The hello-world page |
| `vercel.json` | Build settings + SPA rewrite to `index.html` |
