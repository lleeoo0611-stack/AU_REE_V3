# AU REE V3 research website

The static app uses the existing 93×51 Census contract and files under `data/releases/<id>/`. It checks the digest and release identity of each file before displaying a version. The default mode reads `data/latest.json`. The controlled preview mode reads `data/preview.json` after the user opens `?preview=1`. Both modes support `release`, `tab`, and `project` URL parameters. The app checks for a new version every 10 minutes while visible. Set `refresh=0` to disable polling or use a value between 5 and 60 minutes.

Work on the copy produced by the V3 site build, where the static files and the verified data appear together. For local use serve this assembled directory over HTTP.

```sh
python -m http.server 8000 --bind 127.0.0.1 --directory work/census-web/preview
```

Open `http://127.0.0.1:8000/?preview=1` for a candidate and the same path without the query for formal data. On a GitHub Pages project site, paths are relative to the site subdirectory. Web Crypto uses HTTPS on remote origins.

The supplied `rare-earth-projects_v8_2026-07-20.zip` informed the map filter sidebar, grouped element comparison and language controls. Its inline data, identifiers and external basemap dependencies are outside this application. The reference ZIP SHA-256 is `f289607b17bb5839eaa8db224495e45ca47ca63c156262f1d835b2721460b8b0`.

The Australia outline is Natural Earth public domain [1:110m, release 5.1.2](https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_110m_admin_0_countries.geojson). Its file records the upstream SHA. Qualified site and deposit points come from the separate research GIS package.

Browser CI uses versioned synthetic data. To exercise two real candidate exports locally, use the candidate data directory that has both releases. The browser test serves the package through Playwright interception without exposing the candidate preview on a port. The test verifies the downloaded CSV digest against the input file.

```sh
cd web
npm ci
npx --no-install playwright install chromium
npm test
node tests/browser.mjs ../work/census-web/preview/data
```
