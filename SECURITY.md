# Security Policy

## Supported Version

ApplyTeX ATS is currently an MVP. Security fixes are applied to the latest
version on the default branch.

## Reporting A Vulnerability

Please do not open a public issue for vulnerabilities involving:

- arbitrary file access or command execution through LaTeX;
- secret exposure;
- provider credentials;
- resume or job-description data leakage;
- cross-user access through the API;
- prompt injection that bypasses claim validation.

Contact the repository owner privately through their GitHub profile and include
reproduction steps, impact, and suggested mitigation when possible.

## Development Deployment Warning

The FastAPI service and Streamlit UI are intended for local development. Resume
sessions remain in memory and the API has no authentication. It binds to
`127.0.0.1` by default and limits browser CORS access to the local UI and Chrome
extension origins. Do not expose it directly to the public internet or use it as
a multi-user service.

Uploaded LaTeX is untrusted. Run compilation in an isolated environment before
using this project in production.

## Local Profile Data

Candidate contact details, authorization answers, and voluntary EEO answers are
stored only in the gitignored `.applytex/` SQLite database. The MVP does
not yet encrypt that database at rest. Do not commit, upload, or share it.
