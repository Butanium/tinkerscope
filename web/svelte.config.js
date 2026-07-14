import adapter from '@sveltejs/adapter-static';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  kit: {
    // Single client-rendered route → emit a static SPA into web/dist.
    // FastAPI serves web/dist directly; there is no node server.
    adapter: adapter({
      pages: 'dist',
      assets: 'dist',
      fallback: 'index.html',
      precompress: false,
      strict: true
    })
  }
};

export default config;
