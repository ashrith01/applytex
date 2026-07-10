# ApplyTeX ATS Chrome Extension

This Manifest V3 extension is the user-visible browser bridge for ApplyTeX ATS.
The current version supports reviewed filling:

- capture the current LinkedIn, Greenhouse, Lever, or Ashby job;
- scan visible application fields and their labels;
- send the result to the local FastAPI service;
- preview whether required answers remain unresolved;
- fill only the reviewed actions after a separate user click.

It cannot submit applications. The user reviews the completed page and clicks
the employer's Submit button manually.

## Local Installation

1. Start the API with `uv run applytex-api` and the web UI with `cd frontend && npm run dev`.
2. Open `chrome://extensions`.
3. Enable Developer mode.
4. Choose **Load unpacked** and select this `extension` directory.
5. Open a supported job page and select the ApplyTeX ATS extension.

The extension communicates with `http://127.0.0.1:8000` and opens the web UI at `http://localhost:3000` for guided resume tailoring.
