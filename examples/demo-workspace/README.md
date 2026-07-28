# Demo workspace

This is a minimal source example for local development. It contains no
`node_modules`, credentials, customer data, or private service addresses.

From this directory:

```bash
npm ci --ignore-scripts
npx playwright install chromium
npm test
```

The root `config.example.json` points to this workspace. Docker Compose uses
`deploy/config.example.json` and creates its own persistent workspace volume.
