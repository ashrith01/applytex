"""Local synthetic applications backed by the real ApplyTeX API.

Run: .venv/bin/python scripts/autofill_lab.py
Open: http://127.0.0.1:8765/lab
Uses its own database; never loads the user's application/profile database.
"""

from __future__ import annotations

import argparse
import base64
import html
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from latex_resume.api import create_app
from latex_resume.application_store import ApplicationStore
from latex_resume.job_models import CandidateProfile, JobProvider

ROOT = Path(__file__).resolve().parents[1]
PROVIDERS = [provider.value for provider in JobProvider if provider.value != "unknown"]
SCENARIOS = ("complete", "edge-cases", "multi-step")
PROFILE_ID = "autofill-lab"


def demo_pdf() -> bytes:
    """Create a tiny valid PDF containing only fictional test information."""
    stream = b"BT /F1 12 Tf 50 740 Td (Avery Morgan - synthetic autofill test resume) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    data += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    return data + f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()


def synthetic_profile() -> CandidateProfile:
    """Explicit facts and deliberate unknowns for ground-truth assertions."""
    return CandidateProfile.model_validate({
        "profile_id": PROFILE_ID, "full_name": "Avery Morgan", "first_name": "Avery", "last_name": "Morgan",
        "email": "avery@example.test", "phone": "+1 202-555-0142", "location": "Austin, TX",
        "address": {"line1": "100 Example Street", "city": "Austin", "state": "Texas", "postal_code": "78701", "country": "United States"},
        "linkedin_url": "https://www.linkedin.com/in/synthetic-avery",
        "github_url": "https://github.com/synthetic-avery", "portfolio_url": "https://avery.example.test",
        "skills": ["Python", "SQL", "Java"],
        "work_authorization": {"authorized_to_work_in_us": True, "current_requires_sponsorship": False, "future_requires_sponsorship": True},
        "application_facts": {"is_at_least_18": True, "willing_to_relocate": False, "willing_to_travel": True},
        "education": {"school": "Example University", "degree": "Master of Science", "degree_level": "MS", "major": "Computer Science", "start_date": "2024-08", "end_date": "2026-05", "graduation_month": "May", "graduation_year": "2026"},
        "educations": [{"school": "Example University", "degree": "Master of Science", "degree_level": "MS", "major": "Computer Science", "start_date": "2024-08", "end_date": "2026-05"}],
        "work_experiences": [{"company": "Example Analytics", "job_title": "Data Engineer", "start_date": "2023-06", "end_date": "2024-07", "summary": "Built tested SQL pipelines."}],
        "custom_answers": {"Preferred programming language": "Java", "Earliest start date": "2026-10-01", "Why this role": "I have built tested SQL pipelines and Python evaluation tools, and want to apply those skills to this role."},
        "equal_opportunity": {"allow_autofill": False, "gender": "Non-binary"},
        "resume_filename": "synthetic-resume.pdf", "resume_pdf_filename": "synthetic-resume.pdf",
        "resume_pdf_b64": base64.b64encode(demo_pdf()).decode(),
    })


def field(label: str, key: str, kind: str = "text", *, options: list[str] | None = None, required: bool = True, value: str = "") -> str:
    prompt = html.escape(label) + ("*" if required else "")
    attrs = f'id="{key}" name="{key}"' + (" required" if required else "")
    if options is not None:
        control = f'<select {attrs}><option value="">Select one</option>' + "".join(f'<option>{html.escape(option)}</option>' for option in options) + "</select>"
    elif kind == "textarea":
        control = f'<textarea {attrs}>{html.escape(value)}</textarea>'
    else:
        control = f'<input {attrs} type="{kind}" value="{html.escape(value, quote=True)}">'
    return f'<label for="{key}">{prompt}{control}</label>'


def application_html(provider: str, scenario: str) -> str:
    """Render contrasting ATS control patterns using fictional data only."""
    edge = scenario == "edge-cases"
    contact = "".join([
        field("First name", "first_name", value="Already reviewed" if edge else ""), field("Last name", "last_name"),
        field("Email address", "email", "email"), field("Phone", "phone", "tel"),
        field("City", "city"), field("State", "state", options=["California", "Texas", "New York"]),
        field("Country", "country", options=["Canada", "United States", "United Kingdom"]),
        field("ZIP/postal code", "postal_code"), field("LinkedIn URL", "linkedin_url"),
        field("Resume/CV", "resume", "file"),
        '<fieldset aria-label="Cover Letter*"><legend>Cover Letter*</legend><div><label for="cover_file">Attach document</label><input id="cover_file" type="file" accept=".pdf,.txt" style="display:none"></div></fieldset>',
    ])
    eligibility = "".join([
        field("Are you legally authorized to work in the United States?", "authorized", options=["Yes", "No"]),
        field("Do you currently require sponsorship for work visa status?", "current_sponsorship", options=["Yes", "No"]),
        field("Will you in the future require sponsorship for work visa status?", "future_sponsorship", options=["No", "Yes"]),
        field("Are you willing to relocate?", "relocation", options=["Prefer not to answer", "No", "Yes"]),
        field("Are you willing to travel if required by the position?", "travel", options=["No", "Yes"]),
        field("Preferred programming language", "language", options=["JavaScript", "Java"] if not edge else ["JavaScript", "TypeScript"]),
        field("What is your earliest available start date?", "start_date", "date", required=False),
        field("Gender", "gender", options=["Male", "Female", "Non-binary", "Prefer not to answer"], required=False),
        field("How many years of Rust experience do you have?", "unknown_years", required=True),
        field("Why are you interested in this role?", "why_role", "textarea", required=False),
    ])
    if edge:
        eligibility += field("Are you legally authorized to work in the United States?", "ambiguous_authorization", options=["Yes, with restrictions", "Yes, without restrictions", "No"])
    if provider == "workday":
        eligibility += '<section role="group"><p>Are you at least 18 years of age?*</p><button type="button" id="age" role="combobox" aria-haspopup="listbox" aria-expanded="false" aria-label="Select One Required">Select One</button></section>'
    if provider == "ashby":
        eligibility += '<fieldset class="ashby-application-form-field-entry"><legend class="ashby-application-form-question-title">Which skills do you use?</legend><label><input type="checkbox" name="lab_skill" value="Python">Python</label><label><input type="checkbox" name="lab_skill" value="SQL">SQL</label><label><input type="checkbox" name="lab_skill" value="Rust">Rust</label></fieldset>'
    multistep = scenario == "multi-step"
    form = f'<section id="contact-step">{contact}</section><section id="question-step" {"hidden" if multistep else ""}>{eligibility}</section>'
    if multistep:
        form += '<button type="button" id="next-step">Save and Continue</button>'
    form += '<button type="submit" id="final-submit">Submit application</button>'
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta property="og:site_name" content="Example Research Labs"><title>ML Engineer at Example Research Labs</title><link rel="stylesheet" href="/lab/style.css"></head>
<body data-lab-provider="{provider}" data-lab-scenario="{scenario}"><aside class="lab-banner"><a href="/lab">Autofill lab</a> · Fictional application · {html.escape(provider)} / {html.escape(scenario)} · No employer receives these answers</aside>
<header><div class="company-name">Example Research Labs</div><h1 class="app-title job-title">Machine Learning Engineer</h1><div class="location">Austin, TX</div></header>
<main id="content" class="job__description posting-description"><section id="job-details" class="jobs-description__content" data-automation-id="jobPostingDescription">Example Research Labs is hiring a Machine Learning Engineer. Work on Python and SQL pipelines, model evaluation, data quality, APIs, monitoring and reliable deployments. This fictional role supports a synthetic application test. No employer account is created and no external application is submitted.</section>
<div data-automation-id="progressBarActiveStep">{"My Information" if multistep else "Application Questions"}</div><form id="application-form" class="ashby-application-form-container">{form}</form><output id="submit-count">Test submissions: 0</output></main>
<script src="/lab/controls.js"></script><script src="/lab/extension/providers.js"></script><script src="/lab/runtime.js"></script>
{''.join(f'<script src="/lab/extension/{name}"></script>' for name in ('panel-shared.js', 'panel-scan.js', 'panel-fill.js', 'panel-workday.js', 'panel-profile.js', 'panel.js'))}</body></html>'''


STYLE = """body{font:16px system-ui;color:#172a32;margin:0;padding:28px 430px 50px 32px;background:#f3f6f7}a{color:#126566}h1{font-size:30px}.lab-banner{padding:16px;background:#d8f3e8;margin-bottom:24px}form,label{display:grid;gap:10px}label,fieldset,section[role=group]{padding:12px;background:white;border:1px solid #cbd6da;border-radius:8px}input,select,textarea,button{font:inherit;padding:9px}section[hidden]{display:none!important}input[type=checkbox]{width:auto}textarea{min-height:70px}#job-details{padding:20px 0}.cards{display:grid;gap:16px}article{padding:16px;background:white}article a{margin-right:14px}output{display:block;padding:20px}[role=listbox]{background:white;padding:10px;border:1px solid #888}[role=option]{padding:12px;cursor:pointer}@media(max-width:900px){body{padding:20px}}"""

CONTROLS = """let submits=0;document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();document.querySelector('#submit-count').textContent=`Test submissions: ${++submits}`;});document.querySelector('#next-step')?.addEventListener('click',()=>{document.querySelector('#contact-step').remove();document.querySelector('#question-step').hidden=false;document.querySelector('[data-automation-id=progressBarActiveStep]').textContent='Application Questions';document.querySelector('#next-step').remove();});document.querySelector('#age')?.addEventListener('click',()=>{if(document.querySelector('#age-options'))return;const button=document.querySelector('#age');button.setAttribute('aria-expanded','true');const list=document.createElement('div');list.id='age-options';list.setAttribute('role','listbox');for(const answer of ['No','Yes']){const option=document.createElement('div');option.setAttribute('role','option');option.textContent=answer;option.onclick=()=>{button.textContent=answer;button.setAttribute('aria-expanded','false');list.remove();};list.append(option);}button.after(list);});"""

RUNTIME = """(() => {
  const provider=document.body.dataset.labProvider;
  const original=globalThis.ApplyTexProviders;
  globalThis.ApplyTexProviders=Object.freeze({...original,providerForUrl:()=>provider});
  const state={applytexExtensionProfileId:'autofill-lab'};
  window.chrome={runtime:{id:'applytex-synthetic-lab',onMessage:{addListener(){}},async sendMessage(message){
    if(message.type!=='APPLYTEX_API_REQUEST')return {ok:false,status:400,error:'Unsupported lab operation'};
    const options={...message.options};
    if(options.body){
      const body=JSON.parse(options.body);
      // Transport-only URL mapping satisfies the production HTTPS invariant.
      // These reserved test addresses are stored, never requested over network.
      for(const key of ['source_url','apply_url','page_url']){
        if(body[key]?.startsWith(location.origin)) body[key]='https://applytex-lab.example.test'+new URL(body[key]).pathname;
      }
      options.body=JSON.stringify(body);
    }
    const response=await fetch(message.path,{...options,headers:{...options.headers,'Content-Type':'application/json','X-Profile-Id':'autofill-lab'}});
    const text=await response.text();let data;try{data=JSON.parse(text)}catch{data={detail:text}}
    return {ok:response.ok,status:response.status,data,error:response.ok?'':data.detail};
  }},storage:{local:{
    async get(keys){if(Array.isArray(keys))return Object.fromEntries(keys.map(key=>[key,state[key]]));if(typeof keys==='string')return {[keys]:state[keys]};return {...keys,...state}},
    async set(values){Object.assign(state,values)},
    async remove(keys){for(const key of Array.isArray(keys)?keys:[keys])delete state[key]}
  }}};
})();"""


def create_lab(db_path: Path) -> FastAPI:
    store = ApplicationStore(db_path)
    if not any(profile.profile_id == PROFILE_ID for profile in store.list_candidate_profiles()):
        store.save_candidate_profile(synthetic_profile())
    app = create_app(application_store=store)

    @app.middleware("http")
    async def isolate_lab(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        path = request.url.path
        allowed = path.startswith(("/lab", "/profile", "/applications", "/extension/forms", "/extension/jobs/capture", "/extension/resume/prepare", "/health")) or (path == "/auth/status" and request.method == "GET")
        if not allowed or path.endswith(("/answer-draft", "/sync/github")):
            return JSONResponse({"detail": "The synthetic lab disables external searches, model calls and account operations."}, status_code=403)
        if path == "/extension/resume/prepare" and request.method == "POST":
            body = await request.json()
            if body.get("customize", True) is not False:
                return JSONResponse({"detail": "Custom model generation is disabled in the synthetic lab."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/lab/extension", StaticFiles(directory=ROOT / "extension"), name="lab-extension")

    @app.get("/lab", response_class=HTMLResponse)
    def index() -> str:
        cards = "".join(f'<article><strong>{provider}</strong><p>' + "".join(f'<a href="/lab/{provider}/{scenario}">{scenario}</a>' for scenario in SCENARIOS) + "</p></article>" for provider in PROVIDERS)
        return f'<html><head><title>ApplyTeX Autofill Lab</title><link rel="stylesheet" href="/lab/style.css"></head><body><h1>Autofill evaluation lab</h1><p>Synthetic profile: Avery Morgan. These pages use the real extension panel and API in an isolated test database. Missing facts and ambiguous choices should remain unanswered.</p><p><a href="/lab/profile.json">View the synthetic profile</a></p><div class="cards">{cards}</div></body></html>'

    @app.get("/lab/profile.json")
    def profile_json() -> dict[str, object]:
        return synthetic_profile().model_dump(exclude={"resume_pdf_b64"})

    @app.get("/lab/style.css")
    def style() -> Response:
        return Response(STYLE, media_type="text/css")

    @app.get("/lab/runtime.js")
    def runtime() -> Response:
        return Response(RUNTIME, media_type="text/javascript")

    @app.get("/lab/controls.js")
    def controls() -> Response:
        return Response(CONTROLS, media_type="text/javascript")

    @app.get("/lab/{provider}/{scenario}", response_class=HTMLResponse)
    def application(provider: str, scenario: str) -> str:
        if provider not in PROVIDERS or scenario not in SCENARIOS:
            raise HTTPException(404, "Unknown lab case")
        return application_html(provider, scenario)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=ROOT / ".applytex/autofill-lab/lab.db")
    args = parser.parse_args()
    os.environ["APPLYTEX_REQUIRE_AUTH"] = "0"
    uvicorn.run(create_lab(args.db), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
