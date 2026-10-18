# quorus.dev

The public site: one static page. `npm install`, then `npm run dev` to work on it,
`npm run lint` and `npm run build` to check it. Rules for its content are in
[AGENTS.md](AGENTS.md).

## Deploy

The build reads two files outside this directory: `../llms.txt` (served as
`/llms.txt` and `/agents.md`) and `../pyproject.toml` (the footer's version). So the
site must build from a checkout of the whole repository with this directory as the
root, never from an upload of `website/` alone, or `npm run build` fails.

On Vercel (project `murmur-ai-website`): Root Directory = `website`, with "Include
files outside of the Root Directory in the Build Step" left on. `vercel.json` sets the
build command, the output directory and the security headers. From a checkout:

```bash
cd website && npm install && npm run build   # check it builds first
cd .. && vercel deploy                       # a preview, from the repo root
```

Production goes out only after a human has looked at the preview.
