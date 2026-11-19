// Checks top_opportunities_for_alum (sql/014_better_matching.sql) against
// tests/matching_cases.json, the same cases digest.rank() is tested on.
// Optional developer test using an ephemeral PostgreSQL engine:
//   npm install --no-save @electric-sql/pglite
//   node tests/test_matching_sql.cjs
// PGLITE_MODULE can point to an already installed package outside this project.
const {PGlite} = require(process.env.PGLITE_MODULE || '@electric-sql/pglite');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.join(__dirname, '..');

(async () => {
  const db = new PGlite();
  const cases = JSON.parse(fs.readFileSync(path.join(__dirname, 'matching_cases.json'), 'utf8'));
  // Only the columns the function reads.
  await db.exec(`
    create table alumni_intake_responses (id uuid primary key default gen_random_uuid(), tags jsonb);
    create table opportunities (
      id uuid primary key default gen_random_uuid(), title text, type text, organization text,
      url text, deadline date, rolling boolean, status text, source text, tags text[],
      created_at timestamptz);`);
  // 012 first, then 014 replaces it, as in Supabase.
  for (const file of ['012_top_opportunities.sql', '014_better_matching.sql']) {
    await db.exec(fs.readFileSync(path.join(root, 'sql', file), 'utf8'));
  }
  for (const o of cases.opportunities) {
    await db.query(`insert into opportunities (title, type, source, status, tags, deadline, created_at)
                    values ($1, $2, $3, $4, $5, $6, timestamptz '2026-01-01' + $7 * interval '1 day')`,
      [o.title, o.type, o.source, o.status || 'pending', o.tags, o.deadline, o.created]);
  }
  let checks = 0;
  for (const [name, tags] of Object.entries(cases.alumni)) {
    const id = (await db.query('insert into alumni_intake_responses (tags) values ($1) returning id',
      [tags])).rows[0].id;
    const rows = (await db.query('select * from top_opportunities_for_alum($1, 10)', [id])).rows;
    assert.deepEqual(rows.map(r => [r.title, r.score, r.matched_tags]), cases.expected[name], name);
    checks++;
  }
  // Default limit is 5 and default minimum score is 2.
  const student = (await db.query(`select id from alumni_intake_responses
                                   where tags -> 'career_stage' ? 'Student' limit 1`)).rows[0].id;
  assert.equal((await db.query('select * from top_opportunities_for_alum($1)', [student])).rows.length, 4);
  checks++;
  console.log(`matching SQL: ${checks} checks passed`);
  await db.close();
})().catch(err => { console.error(err); process.exit(1); });
