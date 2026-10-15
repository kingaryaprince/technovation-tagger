// Optional developer test: node tests/test_form_adapter.cjs
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.join(__dirname, '..');
const c = vm.createContext({console, Date});
vm.runInContext(fs.readFileSync(path.join(root,'apps_script/updated_form_to_supabase.gs'),'utf8'),c);
const fields = JSON.parse(fs.readFileSync(path.join(root,'data/form_fields.json')));
const scriptFields = JSON.parse(vm.runInContext('JSON.stringify(INTAKE_FIELDS)', c));
assert.deepEqual(scriptFields, fields);
assert.equal(c.normalizedTitle_('  Full   Name '), 'full name');
assert.equal(c.normalizedTitle_("What’s your level?"), "what's your level?");
function item(title, type='TEXT', rows=[]) {
  return {getId:()=>1, getTitle:()=>title, getType:()=>type,
    asGridItem:()=>({getRows:()=>rows}), asCheckboxGridItem:()=>({getRows:()=>rows})};
}
function ir(title,value,type='TEXT',rows=[]) {
  return {getItem:()=>item(title,type,rows), getResponse:()=>value};
}
const response = {getId:()=> 'response-1', getTimestamp:()=>new Date('2026-10-08T12:00:00Z'),
  getItemResponses:()=>[
    ir('Full Name', 'Test Alum'), ir('Email ', ' TEST1@EXAMPLE.COM '),
    ir(fields.languages,['English','Spanish'],'CHECKBOX'),
    ir(fields.topic_familiarity,['Deeply experienced',''],'GRID',['Engineering','Leadership']),
    ir('Future question', 'preserve this')
  ]};
const payload = JSON.parse(JSON.stringify(c.payloadForResponse_(response)));
assert.equal(payload.p_email,'test1@example.com');
assert.deepEqual(payload.p_answers.languages,['English','Spanish']);
assert.deepEqual(payload.p_answers.topic_familiarity,{Engineering:'Deeply experienced',Leadership:''});
assert.equal(payload.p_answers._original_items.at(-1).answer,'preserve this');
assert.deepEqual(JSON.parse(JSON.stringify(c.serializeAnswer_(item('grid','CHECKBOX_GRID',['A','B']),[['x','y']]))),{A:['x','y'],B:''});
assert.throws(()=>c.onUpdatedFormSubmit(), /Submit the actual form/);
assert.throws(()=>c.payloadForResponse_({...response,getItemResponses:()=>[ir('Full Name','A')]}), /Missing or invalid/);
assert.throws(()=>c.payloadForResponse_({...response,getItemResponses:()=>[ir('Email','a@b.com'),ir('Email','x@y.com')]}), /Duplicate/);
console.log('Form adapter checks passed: mapping, titles, arrays, grids, unknown fields, invalid and duplicate inputs.');

// --- Updated form (Oct 13 copy pasted by Arya): auditForm against the real titles ---
const realTitles = JSON.parse(fs.readFileSync(path.join(__dirname, 'updated_form_titles.json')));
const textKeys = ['current_role','project','since_technovation','interests','skills','story_angles'];
function mockForm(titles) {
  const items = titles.map((t, i) => {
    const key = c.fieldLookup_()[c.titleKey_(t)];
    return {getId:()=>i, getTitle:()=>t, getType:()=> textKeys.includes(key) ? 'PARAGRAPH_TEXT' : 'CHECKBOX'};
  });
  return {getItems:()=>items};
}
const logs = [];
c.console = {log: (m) => logs.push(String(m))};
c.PropertiesService = {getScriptProperties:()=>({getProperty:(k)=>({
  SUPABASE_URL:'https://abc.supabase.co', SUPABASE_KEY:'sb_secret_x', FORM_ID:'form-1'})[k]})};
c.FormApp = {openById:()=>mockForm(realTitles)};
c.auditForm();
assert.ok(logs.some(l => l.startsWith('Matched 28 of 28')), logs.join('\n'));
assert.ok(!logs.some(l => l.startsWith('Extra form questions')), logs.join('\n'));
// Old colon wording still maps to the same key.
assert.equal(c.fieldLookup_()[c.titleKey_('If you are working: What is your current role, organization, and industry? If you are a student, what is your area of study and institution?')], 'current_role');
// A brand-new optional question only warns.
logs.length = 0;
c.FormApp = {openById:()=>mockForm([...realTitles, 'Brand new question?'])};
c.auditForm();
assert.ok(logs.some(l => l.includes('Brand new question?')));
// Losing a question the tagger needs is a hard error.
c.FormApp = {openById:()=>mockForm(realTitles.filter(t => !t.startsWith('In 2-3 sentences, give')))};
assert.throws(()=>c.auditForm(), /missing required questions: project/);
// Dropping an optional question is fine.
c.FormApp = {openById:()=>mockForm(realTitles.filter(t => t !== 'Birthdate'))};
c.auditForm();
console.log('Updated-form audit checks passed: 28/28 real titles, punctuation-tolerant, optional vs required.');
