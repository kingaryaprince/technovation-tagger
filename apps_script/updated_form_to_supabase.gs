// Updated form -> raw Supabase intake. No OpenAI key belongs here.
// Create a NEW standalone Apps Script project; see README for Script Properties.
// setupUpdatedForm() attaches to FORM_ID and does not create or edit a form.
const INTAKE_FIELDS = {
  "full_name": "Full Name",
  "email": "Email",
  "birthdate": "Birthdate",
  "participation_years": "What years did you participate in Technovation?",
  "current_role": "If you are working, what is your current role, organization, and industry? If you are a student, what is your area of study and institution?",
  "current_location": "What is your current location? (City, State, Country)",
  "origin_location": "What is your location of origin? (City, State, Country)",
  "languages": "What languages are you comfortable speaking in?",
  "story_formats": "How would you like to share your Technovation story?",
  "project": "In 2-3 sentences, give a brief description of the project you worked on at Technovation.",
  "since_technovation": "In 2-3 sentences, tell us what you've been up to since Technovation.",
  "impact": "What has been the most meaningful impact Technovation had on your path?",
  "speaking_types": "Which types of speaking opportunities are you interested in?",
  "topic_familiarity": "What's your level of familiarity with these topics?",
  "expertise_areas": "Do you have expertise in any of these areas?",
  "story_angles": "What specific angles most resonate with your life and professional experience?",
  "interests": "What topics or causes are you most interested in?",
  "opportunities": "What kinds of opportunities do you want to hear about?",
  "interview_experience": "Have you conducted interviews or hosted conversations before?",
  "interview_comfort": "What is your comfort level with the following:",
  "interview_formats": "Do you have any experience with any of these formats?",
  "skills": "Are there any other skills you would like to share?",
  "speaker_interest": "Are you interested in being a speaker for a Technovation event?",
  "timezone": "What is your current location/timezone?",
  "regions": "Are you comfortable with connecting with people in other regions?",
  "audiences": "Which audiences do you feel comfortable speaking to?",
  "availability": "What is your availability?",
  "notes": "Anything else you would like us to know about you?"
};

// Questions the tagger or the database cannot work without. Losing any other
// question only logs a warning; its answer is still kept in _original_items.
const REQUIRED_FIELDS = ['full_name', 'email', 'current_role', 'project', 'since_technovation',
                         'interests', 'skills', 'story_angles'];
const TEXT_FIELDS = ['current_role', 'project', 'since_technovation', 'interests', 'skills', 'story_angles'];

function normalizedTitle_(value) {
  return String(value).trim().replace(/[’‘]/g, "'").replace(/[–—]/g, '-').replace(/\s+/g, ' ').toLowerCase();
}

// Matching key: letters and digits only, so small punctuation edits in the form
// ("If you are working: What..." vs "If you are working, what...") still match.
function titleKey_(value) {
  return normalizedTitle_(value).replace(/[^a-z0-9]/g, '');
}

function settings_() {
  const p = PropertiesService.getScriptProperties();
  const url = (p.getProperty('SUPABASE_URL') || '').replace(/\/+$/, '');
  const key = (p.getProperty('SUPABASE_KEY') || '').trim();
  const formId = (p.getProperty('FORM_ID') || '').trim();
  if (!/^https:\/\/[a-z0-9-]+\.supabase\.co$/.test(url) || !key || !formId) {
    throw new Error('Set SUPABASE_URL, SUPABASE_KEY, and FORM_ID in Script Properties.');
  }
  if (key.startsWith('sb_publishable_')) throw new Error('Use the server-side Supabase secret key.');
  return {url, key, formId};
}

function fieldLookup_() {
  const lookup = {};
  Object.keys(INTAKE_FIELDS).forEach(k => {lookup[titleKey_(INTAKE_FIELDS[k])] = k;});
  return lookup;
}

function auditForm() {
  const form = FormApp.openById(settings_().formId);
  const lookup = fieldLookup_(), seen = {}, unmapped = [];
  form.getItems().forEach(item => {
    const type = String(item.getType());
    if (['PAGE_BREAK', 'SECTION_HEADER', 'IMAGE', 'VIDEO'].includes(type)) return;
    const title = item.getTitle(), key = lookup[titleKey_(title)];
    if (!key) {unmapped.push(title); return;}
    if (seen[key]) throw new Error('Duplicate question mapped to: ' + key);
    seen[key] = true;
    if (TEXT_FIELDS.includes(key) && !['TEXT','PARAGRAPH_TEXT'].includes(type)) {
      throw new Error('Expected a text question for ' + key + ', got ' + type);
    }
  });
  const missing = Object.keys(INTAKE_FIELDS).filter(k => !seen[k]);
  const missingRequired = missing.filter(k => REQUIRED_FIELDS.includes(k));
  console.log('Matched ' + Object.keys(seen).length + ' of ' + Object.keys(INTAKE_FIELDS).length + ' questions.');
  if (missing.length) console.log('Not found in form (OK unless required): ' + missing.join(', '));
  if (unmapped.length) console.log('Extra form questions (saved under _original_items only): ' + unmapped.join(' | '));
  if (missingRequired.length) {
    throw new Error('Form is missing required questions: ' + missingRequired.join(', ') +
      '. Fix the title in INTAKE_FIELDS to match the form, then rerun auditForm.');
  }
  return form;
}

function setupUpdatedForm() {
  const form = auditForm();
  const config = settings_();
  const check = UrlFetchApp.fetch(config.url + '/rest/v1/alumni_intake_responses?select=id&limit=1', {
    method: 'get', headers: headers_(config.key), muteHttpExceptions: true, followRedirects: false
  });
  if (check.getResponseCode() >= 300) throw new Error('Database check failed: HTTP ' + check.getResponseCode());
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'onUpdatedFormSubmit')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('onUpdatedFormSubmit').forForm(form).onFormSubmit().create();
  console.log('Trigger ready. Respondent link: ' + form.getPublishedUrl());
}

function serializeAnswer_(item, response) {
  const type = String(item.getType());
  if (type === 'GRID' || type === 'CHECKBOX_GRID') {
    const rows = type === 'GRID' ? item.asGridItem().getRows() : item.asCheckboxGridItem().getRows();
    const answers = Array.isArray(response) ? response : [];
    const mapped = {};
    rows.forEach((label, index) => {mapped[label] = answers[index] === undefined ? '' : answers[index];});
    return mapped;
  }
  return response; // Checkbox answers stay arrays; text and dates keep Google's values.
}

function payloadForResponse_(response) {
  const lookup = fieldLookup_(), answers = {}, originals = [], seen = {};
  response.getItemResponses().forEach(r => {
    const item = r.getItem(), title = item.getTitle(), key = lookup[titleKey_(title)];
    const value = serializeAnswer_(item, r.getResponse());
    originals.push({item_id: String(item.getId()), title, type: String(item.getType()), answer: value});
    if (key) {
      if (seen[key]) throw new Error('Duplicate question mapped to ' + key);
      seen[key] = true; answers[key] = value;
    }
  });
  // Preserve every question even when a future question is not in the mapping yet.
  answers._original_items = originals;
  const email = String(answers.email || '').trim().toLowerCase();
  const name = String(answers.full_name || '').trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || !name) {
    throw new Error('Missing or invalid name/email. Check the question mapping.');
  }
  return {p_email: email, p_full_name: name, p_answers: answers,
          p_response_id: response.getId(), p_submitted_at: response.getTimestamp().toISOString()};
}

function headers_(key) {
  const headers = {apikey: key};
  if (!key.startsWith('sb_')) headers.Authorization = 'Bearer ' + key;
  return headers;
}

function onUpdatedFormSubmit(e) {
  if (!e || !e.response) throw new Error('Submit the actual form to test; do not Run this handler manually.');
  sendResponse_(e.response);
}

function sendResponse_(response) {
  const config = settings_();
  const payload = payloadForResponse_(response);
  for (let attempt = 0; attempt < 3; attempt++) {
    const result = UrlFetchApp.fetch(config.url + '/rest/v1/rpc/ingest_alumni_intake', {
      method: 'post', contentType: 'application/json', headers: headers_(config.key),
      payload: JSON.stringify(payload), muteHttpExceptions: true, followRedirects: false
    });
    const status = result.getResponseCode();
    if (status < 300) {console.log('Saved intake; tagging pending.'); return;}
    if (![429,500,502,503,504].includes(status) || attempt === 2) {
      throw new Error('Supabase intake failed: HTTP ' + status + '. Check key, mapping and SQL migration.');
    }
    Utilities.sleep(1000 * Math.pow(2, attempt));
  }
}

// Recovery for a specific failed submission; no broad automatic replay of real profiles.
// Set RETRY_RESPONSE_ID in Script Properties to the form response ID, then Run.
function retryOneResponse() {
  const p = PropertiesService.getScriptProperties();
  const responseId = (p.getProperty('RETRY_RESPONSE_ID') || '').trim();
  if (!responseId) throw new Error('Set RETRY_RESPONSE_ID for the specific response to retry.');
  sendResponse_(FormApp.openById(settings_().formId).getResponse(responseId));
}
