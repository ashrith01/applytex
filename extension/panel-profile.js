(() => {
  const SECTIONS = Object.freeze([
    { id: "personal", label: "Personal" },
    { id: "education", label: "Education" },
    { id: "work", label: "Work experience" },
    { id: "skills", label: "Skills" },
    { id: "eligibility", label: "Eligibility" },
    { id: "eeo", label: "Equal employment" },
    { id: "preferences", label: "Preferences & answers" },
  ]);

  const TARGET_ROLES = Object.freeze([
    ["ai_intern", "AI intern"],
    ["ml_intern", "ML intern"],
    ["nlp_intern", "NLP intern"],
    ["agentic_ai_intern", "Agentic AI intern"],
    ["data_science_intern", "Data science intern"],
    ["ai_engineer", "AI engineer"],
    ["ml_engineer", "ML engineer"],
    ["data_scientist", "Data scientist"],
  ]);

  const REUSABLE_ANSWERS = Object.freeze([
    ["Phone device type", "Phone device type", "Mobile"],
    ["Earliest start date", "Earliest available start date", "Immediately or YYYY-MM-DD"],
    ["Reliable commute", "Can reliably commute", "Yes, No, or a short note"],
    ["Previously employed by company", "Previously employed by a company", "Yes or No"],
    ["Security clearance", "Active security clearance", "Yes or No"],
    ["Available to work weekends", "Available to work weekends", "Yes or No"],
    ["Onsite availability", "Able to work onsite or hybrid", "Yes or No"],
    ["SMS consent", "Application SMS consent", "Yes or No"],
    ["Relevant project link", "Most relevant project link", "https://"],
    ["How did you hear about us?", "How did you hear about the company?", "Job board, referral, event"],
    ["Why this role", "Reusable role-interest answer", "Evidence-based wording you can defend"],
    ["Additional application context", "Additional application context", "Availability or other reusable facts"],
  ]);

  const EEO_OPTIONS = Object.freeze({
    disability: ["Yes", "No", "Decline to self-identify"],
    veteran_status: ["I am a protected veteran", "I am not a protected veteran", "Decline to self-identify"],
    gender: ["Female", "Male", "Non-binary", "Decline to self-identify"],
    lgbtq: ["Yes", "No", "Decline to self-identify"],
    hispanic_or_latino: ["Yes", "No", "Decline to self-identify"],
    race: [
      "American Indian or Alaskan Native",
      "Asian",
      "Black or African American",
      "White",
      "Native Hawaiian or Other Pacific Islander",
      "Two or More Races",
      "Decline to self-identify",
    ],
    pronouns: ["He/Him", "She/Her", "They/Them", "Decline to self-identify"],
  });

  function clone(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function emptyEducation() {
    return {
      school: "",
      degree: "",
      degree_level: "",
      major: "",
      field_of_study_candidates: [],
      start_date: "",
      end_date: "",
      currently_studying: false,
      graduation_month: "",
      graduation_year: "",
      gpa: "",
    };
  }

  function emptyWorkExperience() {
    return {
      job_title: "",
      company: "",
      job_type: "",
      location: "",
      start_date: "",
      end_date: "",
      currently_working: false,
      summary: "",
      bullets: [],
    };
  }

  function normalizeProfile(profile) {
    const next = clone(profile || {});
    [
      "full_name", "first_name", "last_name", "email", "phone", "location",
      "linkedin_url", "portfolio_url", "github_url",
    ].forEach((key) => {
      if (next[key] == null) next[key] = "";
    });
    next.address = next.address || {};
    ["line1", "line2", "city", "county", "state", "postal_code", "country"].forEach((key) => {
      if (next.address[key] == null) next.address[key] = "";
    });
    next.educations = Array.isArray(next.educations) && next.educations.length
      ? next.educations
      : next.education?.school ? [next.education] : [];
    next.education = next.education || emptyEducation();
    next.work_experiences = Array.isArray(next.work_experiences) ? next.work_experiences : [];
    next.skills = Array.isArray(next.skills) ? next.skills : [];
    next.work_authorization = next.work_authorization || {};
    next.equal_opportunity = next.equal_opportunity || {};
    next.equal_opportunity.sexual_orientation = asList(next.equal_opportunity.sexual_orientation);
    next.application_facts = next.application_facts || {};
    next.application_facts.company_relationships = next.application_facts.company_relationships || {};
    next.application_facts.compensation_preferences = Array.isArray(next.application_facts.compensation_preferences)
      ? next.application_facts.compensation_preferences
      : [];
    next.search_preferences = next.search_preferences || {};
    next.search_preferences.target_roles = asList(next.search_preferences.target_roles);
    next.search_preferences.preferred_locations = asList(next.search_preferences.preferred_locations);
    next.search_preferences.accepted_employment_types = asList(next.search_preferences.accepted_employment_types);
    next.search_preferences.excluded_title_terms = asList(next.search_preferences.excluded_title_terms);
    next.custom_answers = next.custom_answers || {};
    return next;
  }

  function asList(value) {
    if (Array.isArray(value)) return value.map((item) => String(item || "").trim()).filter(Boolean);
    if (typeof value === "string") return value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean);
    return [];
  }

  function getPath(target, path) {
    return String(path || "").split(".").reduce((value, key) => value?.[key], target);
  }

  function setPath(target, path, value) {
    const parts = String(path || "").split(".").filter(Boolean);
    if (!parts.length) return target;
    let current = target;
    parts.slice(0, -1).forEach((part, index) => {
      const nextPart = parts[index + 1];
      if (current[part] == null) current[part] = /^\d+$/.test(nextPart) ? [] : {};
      current = current[part];
    });
    current[parts.at(-1)] = value;
    return target;
  }

  function toggleListValue(target, path, value, checked) {
    const current = asList(getPath(target, path));
    const next = checked
      ? Array.from(new Set([...current, value]))
      : current.filter((item) => item !== value);
    return setPath(target, path, next);
  }

  function addRecord(target, kind) {
    if (kind === "education") target.educations.push(emptyEducation());
    if (kind === "work") target.work_experiences.push(emptyWorkExperience());
    return target;
  }

  function removeRecord(target, kind, index) {
    const records = kind === "education" ? target.educations : target.work_experiences;
    if (!Array.isArray(records)) return target;
    records.splice(index, 1);
    return target;
  }

  function moveRecord(target, kind, index, direction) {
    const records = kind === "education" ? target.educations : target.work_experiences;
    if (!Array.isArray(records)) return target;
    const destination = index + direction;
    if (index < 0 || destination < 0 || index >= records.length || destination >= records.length) return target;
    const [record] = records.splice(index, 1);
    records.splice(destination, 0, record);
    return target;
  }

  function setCompensationField(target, employmentType, field, value) {
    const facts = target.application_facts;
    const records = facts.compensation_preferences || [];
    let record = records.find((item) => !item.application_id && item.employment_type === employmentType);
    if (!record) {
      record = {
        application_id: null,
        employment_type: employmentType,
        amount: "",
        currency: "USD",
        period: employmentType === "internship" ? "hourly" : "annual",
      };
      records.push(record);
    }
    record[field] = value;
    facts.compensation_preferences = records;
    return target;
  }

  function patchForSection(section, rawDraft) {
    const draft = normalizeProfile(rawDraft);
    if (section === "personal") {
      return pick(draft, [
        "full_name", "first_name", "last_name", "email", "phone", "location",
        "address", "linkedin_url", "github_url", "portfolio_url",
      ]);
    }
    if (section === "education") {
      const educations = draft.educations.map(cleanEducation).filter(hasEducationValue);
      return { education: educations[0] || emptyEducation(), educations };
    }
    if (section === "work") {
      return { work_experiences: draft.work_experiences.map(cleanWork).filter(hasWorkValue) };
    }
    if (section === "skills") {
      return { skills: uniqueStrings(draft.skills) };
    }
    if (section === "eligibility") {
      return {
        work_authorization: pick(draft.work_authorization, [
          "authorized_to_work_in_us", "requires_sponsorship", "current_requires_sponsorship",
          "future_requires_sponsorship", "internship_requires_sponsorship", "full_time_requires_sponsorship",
        ]),
        application_facts: pick(draft.application_facts, [
          "is_at_least_18", "willing_to_relocate", "willing_to_travel",
          "active_non_compete_or_non_solicit",
        ]),
      };
    }
    if (section === "eeo") {
      return {
        equal_opportunity: {
          ...draft.equal_opportunity,
          sexual_orientation: uniqueStrings(draft.equal_opportunity.sexual_orientation),
        },
      };
    }
    return {
      search_preferences: draft.search_preferences,
      custom_answers: cleanStringMap(draft.custom_answers),
      application_facts: {
        compensation_preferences: (draft.application_facts.compensation_preferences || [])
          .filter((item) => item.application_id || String(item.amount || "").trim())
          .map((item) => ({ ...item, amount: String(item.amount || "").trim(), currency: String(item.currency || "USD").trim().toUpperCase() })),
      },
    };
  }

  function validateSection(section, rawDraft) {
    const draft = normalizeProfile(rawDraft);
    const errors = [];
    if (section === "personal") {
      if (draft.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(draft.email.trim())) {
        errors.push("Enter a valid email address.");
      }
      [
        ["LinkedIn", draft.linkedin_url],
        ["GitHub", draft.github_url],
        ["Portfolio", draft.portfolio_url],
      ].forEach(([label, value]) => {
        if (value && !validHttpUrl(value)) errors.push(`${label} must use an http or https URL.`);
      });
      if (draft.phone && draft.phone.replace(/\D/g, "").length < 7) errors.push("Enter a valid phone number.");
    }
    if (section === "education") {
      draft.educations.forEach((record, index) => {
        if (!hasEducationValue(record)) return;
        if (!String(record.school || "").trim()) errors.push(`Education ${index + 1} needs a school name.`);
        validateDateRange(record.start_date, record.end_date, record.currently_studying, `Education ${index + 1}`, errors);
      });
    }
    if (section === "work") {
      draft.work_experiences.forEach((record, index) => {
        if (!hasWorkValue(record)) return;
        if (!String(record.company || "").trim()) errors.push(`Work experience ${index + 1} needs a company.`);
        if (!String(record.job_title || "").trim()) errors.push(`Work experience ${index + 1} needs a job title.`);
        validateDateRange(record.start_date, record.end_date, record.currently_working, `Work experience ${index + 1}`, errors);
      });
    }
    if (section === "preferences") {
      (draft.application_facts.compensation_preferences || []).forEach((record) => {
        if (!record.amount) return;
        const normalizedAmount = String(record.amount).replace(/,/g, "");
        if (!/^\d+(?:\.\d{1,2})?$/.test(normalizedAmount) || Number(normalizedAmount) <= 0) {
          errors.push(`${sentence(record.employment_type)} compensation must be a positive number.`);
        }
        if (!/^[A-Za-z]{3,8}$/.test(String(record.currency || ""))) {
          errors.push(`${sentence(record.employment_type)} compensation needs a valid currency code.`);
        }
      });
      const projectLink = draft.custom_answers["Relevant project link"];
      if (projectLink && !validHttpUrl(projectLink)) errors.push("The relevant project link must use an http or https URL.");
    }
    return Array.from(new Set(errors));
  }

  function renderWorkspace({ profile, section = "personal", errors = [], saving = false, saveStatus = "" }) {
    const draft = normalizeProfile(profile);
    const sectionLabel = SECTIONS.find((item) => item.id === section)?.label || "Personal";
    return `
      <div class="sja-workspace" data-profile-workspace>
        <header class="sja-workspace-head">
          <button class="sja-icon-button" data-action="workspace-back" type="button" aria-label="Back to ApplyTeX">&#8592;</button>
          <div><span>Profile</span><strong>Autofill information</strong></div>
          <button class="sja-icon-button" data-action="close" type="button" aria-label="Close ApplyTeX ATS">x</button>
        </header>
        <nav class="sja-profile-tabs" aria-label="Autofill information sections">
          ${SECTIONS.map((item) => `<button class="${item.id === section ? "active" : ""}" data-profile-section="${item.id}" type="button">${escapeHtml(item.label)}</button>`).join("")}
        </nav>
        <div class="sja-workspace-scroll">
          <div class="sja-workspace-title"><strong>${escapeHtml(sectionLabel)}</strong><span>Changes update this profile after you save.</span></div>
          ${errors.length ? `<div class="sja-status sja-error" role="alert">${errors.map((error) => `<span>${escapeHtml(error)}</span>`).join("")}</div>` : ""}
          ${saveStatus ? `<div class="sja-status sja-success" role="status">${escapeHtml(saveStatus)}</div>` : ""}
          ${renderSection(section, draft)}
        </div>
        <footer class="sja-workspace-footer">
          <button class="sja-secondary-button" data-action="profile-cancel" type="button" ${saving ? "disabled" : ""}>Cancel</button>
          <button data-action="profile-save" type="button" ${saving ? "disabled" : ""}>${saving ? "Saving..." : "Save changes"}</button>
        </footer>
      </div>
    `;
  }

  function renderResumeWorkspace({ resumeInfo = {}, approvedArtifact = null, hasJob = false, busy = "", message = "", error = "" }) {
    const filename = resumeInfo.resume_pdf_filename || resumeInfo.resume_filename || "No profile resume saved";
    const updated = resumeInfo.resume_updated_at ? formatDate(resumeInfo.resume_updated_at) : "Not updated yet";
    return `
      <div class="sja-workspace" data-resume-workspace>
        <header class="sja-workspace-head">
          <button class="sja-icon-button" data-action="workspace-back" type="button" aria-label="Back to ApplyTeX">&#8592;</button>
          <div><span>Application file</span><strong>Choose a resume</strong></div>
          <button class="sja-icon-button" data-action="close" type="button" aria-label="Close ApplyTeX ATS">x</button>
        </header>
        <div class="sja-workspace-scroll">
          ${busy ? `<div class="sja-status" role="status">${escapeHtml(busy)}...</div>` : ""}
          ${message ? `<div class="sja-status sja-success" role="status">${escapeHtml(message)}</div>` : ""}
          ${error ? `<div class="sja-status sja-error" role="alert">${escapeHtml(error)}</div>` : ""}
          <section class="sja-resume-summary">
            <span>Profile resume</span>
            <strong>${escapeHtml(filename)}</strong>
            <small>${escapeHtml(updated)}</small>
          </section>
          <div class="sja-choice-list">
            <button class="sja-choice-row" data-action="use-profile-resume" type="button" ${resumeInfo.has_pdf ? "" : "disabled"}>
              <span class="sja-choice-mark" aria-hidden="true">&#8593;</span>
              <span><strong>Use profile resume</strong><small>${resumeInfo.has_pdf ? "Upload the saved PDF to this application" : "A saved PDF is required"}</small></span>
            </button>
            <button class="sja-choice-row" data-action="customize-start" type="button" ${hasJob && resumeInfo.has_latex_source ? "" : "disabled"}>
              <span class="sja-choice-mark" aria-hidden="true">&#8599;</span>
              <span><strong>Tailor for this job</strong><small>${resumeInfo.has_latex_source ? "Review changes and the one-page PDF in Tailor Studio" : "A LaTeX profile resume is required"}</small></span>
            </button>
          </div>
          ${approvedArtifact ? `
            <section class="sja-approved-resume">
              <span>Approved tailored resume</span>
              <strong>${escapeHtml(approvedArtifact.filename || "Tailored resume PDF")}</strong>
              <button data-action="use-tailored-resume" type="button">Use tailored resume</button>
            </section>
          ` : ""}
          ${!resumeInfo.has_pdf || !resumeInfo.has_latex_source ? `
            <button class="sja-secondary-button sja-manage-resume" data-action="manage-profile-resume" type="button">Manage profile resume</button>
          ` : ""}
          <p class="sja-safety-note">Resume upload stays reviewed. ApplyTeX will not continue or submit the application.</p>
        </div>
      </div>
    `;
  }

  function renderSection(section, profile) {
    if (section === "personal") return renderPersonal(profile);
    if (section === "education") return renderEducation(profile.educations);
    if (section === "work") return renderWork(profile.work_experiences);
    if (section === "skills") return listField("Skills", "skills", profile.skills, "One skill per line");
    if (section === "eligibility") return renderEligibility(profile);
    if (section === "eeo") return renderEeo(profile.equal_opportunity);
    return renderPreferences(profile);
  }

  function renderPersonal(profile) {
    return `
      <div class="sja-profile-form">
        ${textField("Full name", "full_name", profile.full_name, { autocomplete: "name" })}
        <div class="sja-profile-grid">
          ${textField("First name", "first_name", profile.first_name, { autocomplete: "given-name" })}
          ${textField("Last name", "last_name", profile.last_name, { autocomplete: "family-name" })}
        </div>
        ${textField("Email", "email", profile.email, { type: "email", autocomplete: "email" })}
        <div class="sja-profile-grid">
          ${textField("Phone", "phone", profile.phone, { type: "tel", autocomplete: "tel" })}
          ${textField("Current location", "location", profile.location)}
        </div>
        <div class="sja-profile-rule"><span>Address</span></div>
        ${textField("Address line 1", "address.line1", profile.address.line1, { autocomplete: "address-line1" })}
        ${textField("Address line 2", "address.line2", profile.address.line2, { autocomplete: "address-line2" })}
        <div class="sja-profile-grid">
          ${textField("City", "address.city", profile.address.city, { autocomplete: "address-level2" })}
          ${textField("State", "address.state", profile.address.state, { autocomplete: "address-level1" })}
        </div>
        <div class="sja-profile-grid">
          ${textField("Postal code", "address.postal_code", profile.address.postal_code, { autocomplete: "postal-code" })}
          ${textField("Country", "address.country", profile.address.country, { autocomplete: "country-name" })}
        </div>
        ${textField("County", "address.county", profile.address.county)}
        <div class="sja-profile-rule"><span>Links</span></div>
        ${textField("LinkedIn", "linkedin_url", profile.linkedin_url, { type: "url" })}
        ${textField("GitHub", "github_url", profile.github_url, { type: "url" })}
        ${textField("Website or portfolio", "portfolio_url", profile.portfolio_url, { type: "url" })}
      </div>
    `;
  }

  function renderEducation(records) {
    const rows = records.length ? records : [emptyEducation()];
    return `
      <div class="sja-record-list">
        ${rows.map((record, index) => renderEducationRecord(record, index, rows.length)).join("")}
      </div>
      <button class="sja-secondary-button sja-add-record" data-action="profile-add-record" data-record-kind="education" type="button">Add education</button>
    `;
  }

  function renderEducationRecord(record, index, count) {
    const base = `educations.${index}`;
    return `
      <section class="sja-profile-record">
        ${recordHeader("Education", "education", index, count)}
        ${textField("School or university", `${base}.school`, record.school)}
        <div class="sja-profile-grid">
          ${textField("Degree", `${base}.degree`, record.degree)}
          ${textField("Degree level", `${base}.degree_level`, record.degree_level)}
        </div>
        ${textField("Field of study", `${base}.major`, record.major)}
        ${listField("Alternative field names", `${base}.field_of_study_candidates`, record.field_of_study_candidates, "One accepted name per line")}
        <div class="sja-profile-grid">
          ${textField("Start date", `${base}.start_date`, record.start_date, { placeholder: "YYYY-MM" })}
          ${textField("End date", `${base}.end_date`, record.end_date, { placeholder: "YYYY-MM" })}
        </div>
        <div class="sja-profile-grid">
          ${textField("Graduation month", `${base}.graduation_month`, record.graduation_month)}
          ${textField("Graduation year", `${base}.graduation_year`, record.graduation_year)}
        </div>
        ${textField("GPA", `${base}.gpa`, record.gpa)}
        ${checkboxField("Currently studying", `${base}.currently_studying`, record.currently_studying)}
      </section>
    `;
  }

  function renderWork(records) {
    const rows = records.length ? records : [emptyWorkExperience()];
    return `
      <div class="sja-record-list">
        ${rows.map((record, index) => renderWorkRecord(record, index, rows.length)).join("")}
      </div>
      <button class="sja-secondary-button sja-add-record" data-action="profile-add-record" data-record-kind="work" type="button">Add work experience</button>
    `;
  }

  function renderWorkRecord(record, index, count) {
    const base = `work_experiences.${index}`;
    return `
      <section class="sja-profile-record">
        ${recordHeader("Work experience", "work", index, count)}
        <div class="sja-profile-grid">
          ${textField("Company", `${base}.company`, record.company)}
          ${textField("Job title", `${base}.job_title`, record.job_title)}
        </div>
        <div class="sja-profile-grid">
          ${textField("Job type", `${base}.job_type`, record.job_type)}
          ${textField("Location", `${base}.location`, record.location)}
        </div>
        <div class="sja-profile-grid">
          ${textField("Start date", `${base}.start_date`, record.start_date, { placeholder: "YYYY-MM" })}
          ${textField("End date", `${base}.end_date`, record.end_date, { placeholder: "YYYY-MM" })}
        </div>
        ${checkboxField("Currently working here", `${base}.currently_working`, record.currently_working)}
        ${textareaField("Summary", `${base}.summary`, record.summary, "Short reusable role summary")}
        ${listField("Resume bullets", `${base}.bullets`, record.bullets, "One bullet per line")}
      </section>
    `;
  }

  function renderEligibility(profile) {
    const auth = profile.work_authorization;
    const facts = profile.application_facts;
    return `
      <div class="sja-profile-form">
        <p class="sja-profile-guidance">Unknown legal or eligibility answers stay unfilled for review.</p>
        ${triStateField("Authorized to work in the US", "work_authorization.authorized_to_work_in_us", auth.authorized_to_work_in_us)}
        ${triStateField("Currently requires sponsorship", "work_authorization.current_requires_sponsorship", auth.current_requires_sponsorship)}
        ${triStateField("Will require sponsorship in the future", "work_authorization.future_requires_sponsorship", auth.future_requires_sponsorship)}
        ${triStateField("At least 18 years old", "application_facts.is_at_least_18", facts.is_at_least_18)}
        ${triStateField("Willing to relocate", "application_facts.willing_to_relocate", facts.willing_to_relocate)}
        ${triStateField("Willing to travel", "application_facts.willing_to_travel", facts.willing_to_travel)}
        ${triStateField("Active non-compete or non-solicit", "application_facts.active_non_compete_or_non_solicit", facts.active_non_compete_or_non_solicit)}
      </div>
    `;
  }

  function renderEeo(eeo) {
    return `
      <div class="sja-profile-form">
        <p class="sja-profile-guidance">These answers are voluntary and excluded from resume scoring.</p>
        ${checkboxField("Allow voluntary EEO autofill", "equal_opportunity.allow_autofill", eeo.allow_autofill)}
        ${optionField("Disability status", "equal_opportunity.disability", eeo.disability, EEO_OPTIONS.disability)}
        ${optionField("Veteran status", "equal_opportunity.veteran_status", eeo.veteran_status, EEO_OPTIONS.veteran_status)}
        ${optionField("Gender", "equal_opportunity.gender", eeo.gender, EEO_OPTIONS.gender)}
        ${optionField("LGBTQ+", "equal_opportunity.lgbtq", eeo.lgbtq, EEO_OPTIONS.lgbtq)}
        ${optionField("Hispanic or Latino", "equal_opportunity.hispanic_or_latino", eeo.hispanic_or_latino, EEO_OPTIONS.hispanic_or_latino)}
        ${optionField("Race", "equal_opportunity.race", eeo.race, EEO_OPTIONS.race)}
        ${optionField("Pronouns", "equal_opportunity.pronouns", eeo.pronouns, EEO_OPTIONS.pronouns)}
        ${listField("Sexual orientation", "equal_opportunity.sexual_orientation", eeo.sexual_orientation, "One selected value per line")}
      </div>
    `;
  }

  function renderPreferences(profile) {
    const prefs = profile.search_preferences;
    const facts = profile.application_facts;
    return `
      <div class="sja-profile-form">
        <div class="sja-profile-rule"><span>Job search</span></div>
        ${checkListField("Target roles", "search_preferences.target_roles", prefs.target_roles, TARGET_ROLES)}
        ${listField("Preferred locations", "search_preferences.preferred_locations", prefs.preferred_locations, "One location per line")}
        ${listField("Excluded title terms", "search_preferences.excluded_title_terms", prefs.excluded_title_terms, "One term per line")}
        ${checkListField("Employment types", "search_preferences.accepted_employment_types", prefs.accepted_employment_types, [["internship", "Internship"], ["full_time", "Full-time"]])}
        ${checkboxField("Allow remote US", "search_preferences.allow_remote_us", prefs.allow_remote_us)}
        ${checkboxField("Allow hybrid", "search_preferences.allow_hybrid", prefs.allow_hybrid)}
        ${checkboxField("Allow onsite", "search_preferences.allow_onsite", prefs.allow_onsite)}
        ${checkboxField("Prioritize internships", "search_preferences.prioritize_internships", prefs.prioritize_internships)}
        <div class="sja-profile-rule"><span>Compensation defaults</span></div>
        ${["internship", "full_time"].map((type) => renderCompensation(type, facts.compensation_preferences)).join("")}
        <div class="sja-profile-rule"><span>Reusable answers</span></div>
        ${REUSABLE_ANSWERS.map(([key, label, placeholder]) => textareaField(label, `custom_answers.${key}`, profile.custom_answers[key] || "", placeholder, { compact: true })).join("")}
      </div>
    `;
  }

  function renderCompensation(type, records) {
    const record = records.find((item) => !item.application_id && item.employment_type === type) || {
      amount: "", currency: "USD", period: type === "internship" ? "hourly" : "annual",
    };
    return `
      <fieldset class="sja-compensation-row">
        <legend>${type === "internship" ? "Internship" : "Full-time"}</legend>
        <div class="sja-profile-grid sja-profile-grid-three">
          ${compensationField("Amount", type, "amount", record.amount)}
          ${compensationField("Currency", type, "currency", record.currency)}
          ${compensationSelect(type, record.period)}
        </div>
      </fieldset>
    `;
  }

  function recordHeader(label, kind, index, count) {
    return `
      <div class="sja-record-head">
        <strong>${escapeHtml(label)} ${index + 1}</strong>
        <div>
          <button class="sja-record-icon" data-action="profile-move-record" data-record-kind="${kind}" data-record-index="${index}" data-direction="-1" type="button" aria-label="Move ${escapeAttr(label)} ${index + 1} up" ${index === 0 ? "disabled" : ""}>&#8593;</button>
          <button class="sja-record-icon" data-action="profile-move-record" data-record-kind="${kind}" data-record-index="${index}" data-direction="1" type="button" aria-label="Move ${escapeAttr(label)} ${index + 1} down" ${index === count - 1 ? "disabled" : ""}>&#8595;</button>
          <button class="sja-record-icon sja-record-remove" data-action="profile-remove-record" data-record-kind="${kind}" data-record-index="${index}" type="button" aria-label="Remove ${escapeAttr(label)} ${index + 1}">x</button>
        </div>
      </div>
    `;
  }

  function textField(label, path, value, options = {}) {
    const id = fieldId(path);
    return `
      <label class="sja-profile-field" for="${id}">
        <span>${escapeHtml(label)}</span>
        <input id="${id}" data-profile-path="${escapeAttr(path)}" data-value-kind="string" type="${escapeAttr(options.type || "text")}" value="${escapeAttr(value || "")}" ${options.autocomplete ? `autocomplete="${escapeAttr(options.autocomplete)}"` : ""} ${options.placeholder ? `placeholder="${escapeAttr(options.placeholder)}"` : ""} />
      </label>
    `;
  }

  function textareaField(label, path, value, placeholder = "", options = {}) {
    const id = fieldId(path);
    return `
      <label class="sja-profile-field" for="${id}">
        <span>${escapeHtml(label)}</span>
        <textarea id="${id}" class="${options.compact ? "compact" : ""}" data-profile-path="${escapeAttr(path)}" data-value-kind="string" placeholder="${escapeAttr(placeholder)}">${escapeHtml(value || "")}</textarea>
      </label>
    `;
  }

  function listField(label, path, values, placeholder) {
    const id = fieldId(path);
    return `
      <label class="sja-profile-field" for="${id}">
        <span>${escapeHtml(label)}</span>
        <textarea id="${id}" data-profile-path="${escapeAttr(path)}" data-value-kind="list" placeholder="${escapeAttr(placeholder)}">${escapeHtml(asList(values).join("\n"))}</textarea>
      </label>
    `;
  }

  function triStateField(label, path, value) {
    const id = fieldId(path);
    return `
      <label class="sja-profile-field" for="${id}">
        <span>${escapeHtml(label)}</span>
        <select id="${id}" data-profile-path="${escapeAttr(path)}" data-value-kind="tri-state">
          <option value="" ${value == null ? "selected" : ""}>Not answered</option>
          <option value="true" ${value === true ? "selected" : ""}>Yes</option>
          <option value="false" ${value === false ? "selected" : ""}>No</option>
        </select>
      </label>
    `;
  }

  function checkboxField(label, path, value) {
    return `
      <label class="sja-profile-check">
        <input data-profile-path="${escapeAttr(path)}" data-value-kind="checkbox" type="checkbox" ${value ? "checked" : ""} />
        <span>${escapeHtml(label)}</span>
      </label>
    `;
  }

  function optionField(label, path, value, options) {
    const id = fieldId(path);
    const items = value && !options.includes(value) ? [value, ...options] : options;
    return `
      <label class="sja-profile-field" for="${id}">
        <span>${escapeHtml(label)}</span>
        <select id="${id}" data-profile-path="${escapeAttr(path)}" data-value-kind="nullable-string">
          <option value="" ${value == null || value === "" ? "selected" : ""}>Not answered</option>
          ${items.map((option) => `<option value="${escapeAttr(option)}" ${option === value ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
        </select>
      </label>
    `;
  }

  function checkListField(label, path, selected, options) {
    const values = asList(selected);
    return `
      <fieldset class="sja-profile-check-list">
        <legend>${escapeHtml(label)}</legend>
        <div>${options.map(([value, text]) => `
          <label><input data-profile-list-path="${escapeAttr(path)}" data-profile-list-value="${escapeAttr(value)}" type="checkbox" ${values.includes(value) ? "checked" : ""} /><span>${escapeHtml(text)}</span></label>
        `).join("")}</div>
      </fieldset>
    `;
  }

  function compensationField(label, type, field, value) {
    const id = fieldId(`compensation.${type}.${field}`);
    return `
      <label class="sja-profile-field" for="${id}"><span>${escapeHtml(label)}</span><input id="${id}" data-profile-compensation-type="${type}" data-profile-compensation-field="${field}" value="${escapeAttr(value || "")}" /></label>
    `;
  }

  function compensationSelect(type, value) {
    const id = fieldId(`compensation.${type}.period`);
    return `
      <label class="sja-profile-field" for="${id}"><span>Period</span><select id="${id}" data-profile-compensation-type="${type}" data-profile-compensation-field="period">
        ${["hourly", "monthly", "annual"].map((option) => `<option value="${option}" ${option === value ? "selected" : ""}>${sentence(option)}</option>`).join("")}
      </select></label>
    `;
  }

  function pick(source, keys) {
    return Object.fromEntries(keys.filter((key) => Object.prototype.hasOwnProperty.call(source || {}, key)).map((key) => [key, clone(source[key])]));
  }

  function cleanEducation(record) {
    return {
      ...record,
      school: clean(record.school), degree: clean(record.degree), degree_level: clean(record.degree_level),
      major: clean(record.major), field_of_study_candidates: uniqueStrings(record.field_of_study_candidates),
      start_date: clean(record.start_date), end_date: clean(record.end_date),
      graduation_month: clean(record.graduation_month), graduation_year: clean(record.graduation_year), gpa: clean(record.gpa),
    };
  }

  function cleanWork(record) {
    return {
      ...record,
      job_title: clean(record.job_title), company: clean(record.company), job_type: clean(record.job_type),
      location: clean(record.location), start_date: clean(record.start_date), end_date: clean(record.end_date),
      summary: clean(record.summary), bullets: uniqueStrings(record.bullets),
    };
  }

  function cleanStringMap(values) {
    return Object.fromEntries(Object.entries(values || {}).map(([key, value]) => [key, clean(value)]).filter(([, value]) => value));
  }

  function uniqueStrings(values) {
    return Array.from(new Set(asList(values).map(clean).filter(Boolean)));
  }

  function hasEducationValue(record) {
    return Boolean(record && [record.school, record.degree, record.major, record.start_date, record.end_date, record.gpa].some((value) => clean(value)));
  }

  function hasWorkValue(record) {
    return Boolean(record && [record.company, record.job_title, record.location, record.start_date, record.end_date, record.summary, ...(record.bullets || [])].some((value) => clean(value)));
  }

  function validateDateRange(start, end, current, label, errors) {
    if (start && !validDatePart(start)) errors.push(`${label} start date must be YYYY, YYYY-MM, or YYYY-MM-DD.`);
    if (end && !validDatePart(end)) errors.push(`${label} end date must be YYYY, YYYY-MM, or YYYY-MM-DD.`);
    if (!current && start && end && comparableDate(start) > comparableDate(end)) errors.push(`${label} end date cannot be before its start date.`);
  }

  function validDatePart(value) {
    return /^\d{4}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?)?$/.test(String(value).trim());
  }

  function comparableDate(value) {
    const parts = String(value).split("-");
    return `${parts[0]}-${parts[1] || "01"}-${parts[2] || "01"}`;
  }

  function validHttpUrl(value) {
    try {
      const url = new URL(String(value).trim());
      return ["http:", "https:"].includes(url.protocol);
    } catch {
      return false;
    }
  }

  function fieldId(path) {
    return `sja-profile-${String(path).replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "")}`;
  }

  function sentence(value) {
    const text = String(value || "").replaceAll("_", " ");
    return text ? `${text[0].toUpperCase()}${text.slice(1)}` : "";
  }

  function clean(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Updated recently";
    return `Updated ${date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  const escapeAttr = escapeHtml;

  globalThis.ApplyTexPanelProfile = Object.freeze({
    SECTIONS,
    clone,
    normalizeProfile,
    setPath,
    toggleListValue,
    addRecord,
    removeRecord,
    moveRecord,
    setCompensationField,
    patchForSection,
    validateSection,
    renderWorkspace,
    renderResumeWorkspace,
  });
})();
